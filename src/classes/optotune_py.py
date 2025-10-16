import struct
import serial


def crc_16(data):
    """
    Calculate CRC-16 for data validation.

    Args:
        data: Bytes to calculate CRC for

    Returns:
        int: The calculated CRC-16 value
    """
    crc = 0x0000
    for c in data:
        crc = crc ^ c
        for i in range(0, 8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) > 0 else crc >> 1

    return crc


class LensException(Exception):
    """Base exception for all LiquidLens errors."""

    pass


class LensConnectionError(LensException):
    """Raised when there is an error connecting to the lens device."""

    pass


class LensCommandError(LensException):
    """Raised when a command to the lens fails."""

    pass


class LensDriver:
    """
    Interface for controlling a liquid lens device via serial communication.

    This class provides methods to control a liquid lens, including:
    - Getting lens information (firmware, serial number, temperature)
    - Switching between focal power mode and current mode
    - Setting and getting diopter values
    - Setting and getting current values
    - Reading and writing to EEPROM
    - Setting temperature limits
    """

    def __init__(self, port, debug=False):
        """
        Initialize connection to a lens device.

        Args:
            port (str): Serial port name (e.g., 'COM7' on Windows, '/dev/ttyUSB0' on Linux)
            debug (bool, optional): Enable debugging output. Defaults to False.

        Raises:
            LensConnectionError: If connection to the lens device fails
        """
        self.debug = debug

        try:
            self.connection = serial.Serial(port, 115200, timeout=1)
            self.connection.flush()

            self.connection.write(b"Start")
            if not self.connection.readline() == b"Ready\r\n":
                raise LensConnectionError("Lens Driver did not reply to handshake")
        except serial.SerialException as e:
            raise LensConnectionError(
                f"Failed to connect to lens on port {port}: {str(e)}"
            )

        self.firmware_type = self.get_firmware_type()
        self.firmware_version = self.get_firmware_version()

        self.device_id = self.get_device_id()
        self.max_output_current = self.get_max_output_current()
        self.set_temperature_limits(20, 40)

        self.mode = None
        self.refresh_active_mode()

        self.lens_serial = self.get_lens_serial_number()

        if self.debug:
            print(
                "=== Lens initialization complete =================================================================="
            )

    def send_command(self, command, reply_fmt=None):
        """
        Send a command to the lens device and process the response.

        Args:
            command: Command bytes or string to send
            reply_fmt: Format string for struct.unpack to parse response

        Returns:
            tuple: Unpacked response data if reply_fmt is provided

        Raises:
            LensCommandError: If the response is invalid or has incorrect CRC
        """
        if not isinstance(command, bytes):
            command = bytes(command, encoding="ascii")
        command = command + struct.pack("<H", crc_16(command))
        if self.debug:
            commandhex = " ".join("{:02x}".format(c) for c in command)
            print("{:<50} ¦ {}".format(commandhex, command))
        self.connection.write(command)

        if reply_fmt is not None:
            response_size = struct.calcsize(reply_fmt)
            response = self.connection.read(response_size + 4)
            if self.debug:
                responsehex = " ".join("{:02x}".format(c) for c in response)
                print("{:>50} ¦ {}".format(responsehex, response))

            if not response or len(response) < response_size + 4:
                raise LensCommandError("Expected response not received")

            data, crc, newline = struct.unpack(
                "<{}sH2s".format(response_size), response
            )
            if crc != crc_16(data) or newline != b"\r\n":
                raise LensCommandError("Response CRC not correct")

            return struct.unpack(reply_fmt, data)

    def get_max_output_current(self):
        """Get the maximum output current of the lens in mA."""
        return self.send_command("CrMA\x00\x00", ">xxxh")[0] / 100

    def get_firmware_type(self):
        """Get the firmware type of the lens ('A' or other)."""
        return self.send_command("H", ">xs")[0].decode("ascii")

    def get_firmware_branch(self):
        """Get the firmware branch number."""
        return self.send_command("F", ">xB")[0]

    def get_device_id(self):
        """Get the device identifier."""
        return self.send_command("IR\x00\x00\x00\x00\x00\x00\x00\x00", ">xx8s")[
            0
        ].decode("ascii")

    def get_firmware_version(self):
        """Get the firmware version information."""
        return self.send_command(b"V\x00", ">xBBHH")

    def get_lens_serial_number(self):
        """Get the lens serial number."""
        return self.send_command("X", ">x8s")[0].decode("ascii")

    def eeprom_write_byte(self, address, byte):
        """
        Write a byte to the lens EEPROM.

        Args:
            address: EEPROM address (0-255)
            byte: Value to write (0-255)

        Returns:
            int: Status code from the operation
        """
        return self.send_command(b"Zw" + struct.pack("BB", address, byte), ">xB")[0]

    def eeprom_dump(self):
        """
        Read all EEPROM contents.

        Returns:
            list: All 256 bytes from the EEPROM
        """
        return [
            self.send_command(b"Zr" + struct.pack("B", i), ">xB")[0] for i in range(256)
        ]

    def eeprom_print(self):
        """Display EEPROM contents formatted as hex values."""
        eeprom = self.eeprom_dump()

        print("===============================================")
        print("EEPROM of lens number {}".format(self.lens_serial))
        print("===============================================")
        for i in range(16):
            print(
                " ".join(
                    ["{:02x}".format(byte) for byte in eeprom[i * 16 : i * 16 + 16]]
                )
            )
        print("===============================================")

    def get_temperature(self):
        """
        Get the current lens temperature.

        Returns:
            float: Temperature in degrees Celsius
        """
        return self.send_command(b"TCA", ">xxxh")[0] * 0.0625

    def set_temperature_limits(self, lower, upper):
        """
        Set the lens temperature limits.

        Args:
            lower: Lower temperature limit in Celsius
            upper: Upper temperature limit in Celsius

        Returns:
            tuple: (error_code, min_focal_power, max_focal_power)
        """
        error, max_fp, min_fp = self.send_command(
            b"PwTA" + struct.pack(">hh", upper * 16, lower * 16), ">xxBhh"
        )
        if self.firmware_type == "A":
            return error, min_fp / 200 - 5, max_fp / 200 - 5
        else:
            return error, min_fp / 200, max_fp / 200

    def get_current(self):
        """
        Get the current lens current in mA.

        Returns:
            float: Current in mA
        """
        return (
            self.send_command(b"Ar\x00\x00", ">xh")[0] * self.max_output_current / 4095
        )

    def set_current(self, current):
        """
        Set the lens current in mA.

        Args:
            current: Current in mA

        Raises:
            LensCommandError: If not in current mode
        """
        if not self.mode == 1:
            raise LensCommandError("Cannot set current when not in current mode")
        raw_current = int(current * 4095 / self.max_output_current)
        self.send_command(b"Aw" + struct.pack(">h", raw_current))

    def get_diopter(self):
        """
        Get the current lens focal power in diopters.

        Returns:
            float: Focal power in diopters
        """
        (raw_diopter,) = self.send_command(b"PrDA\x00\x00\x00\x00", ">xxh")
        return raw_diopter / 200 - 5 if self.firmware_type == "A" else raw_diopter / 200

    def set_diopter(self, diopter):
        """
        Set the lens focal power in diopters.

        Args:
            diopter: Focal power in diopters

        Raises:
            LensCommandError: If not in focal power mode
        """
        if not self.mode == 5:
            raise LensCommandError(
                "Cannot set focal power when not in focal power mode"
            )
        raw_diopter = int(
            (diopter + 5) * 200 if self.firmware_type == "A" else diopter * 200
        )
        self.send_command(b"PwDA" + struct.pack(">h", raw_diopter) + b"\x00\x00")

    def to_focal_power_mode(self):
        """
        Switch the lens to focal power mode.

        Returns:
            tuple: (min_focal_power, max_focal_power) in diopters
        """
        error, max_fp_raw, min_fp_raw = self.send_command("MwCA", ">xxxBhh")
        min_fp, max_fp = min_fp_raw / 200, max_fp_raw / 200
        if self.firmware_type == "A":
            min_fp, max_fp = min_fp - 5, max_fp - 5

        self.refresh_active_mode()
        return min_fp, max_fp

    def to_current_mode(self):
        """Switch the lens to current mode."""
        self.send_command("MwDA", ">xxx")
        self.refresh_active_mode()

    def refresh_active_mode(self):
        """
        Update the current mode information.

        Returns:
            int: Current mode number
        """
        self.mode = self.send_command("MMA", ">xxxB")[0]
        return self.mode

    def close(self):
        """Close the connection to the lens device."""
        if hasattr(self, "connection") and self.connection:
            self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
