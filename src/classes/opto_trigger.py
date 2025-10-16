"""
OptoTrigger module for controlling Arduino-based optical stimulation.
"""

import os
import random
import time
from typing import Optional

import serial

from src.utils.config import OptoTriggerConfig
from src.classes.csv_writer import CSVWriter
from src.utils.custom_logger import init_class_logger


class OptoTrigger:
    """
    Controls an Arduino-based optical stimulation system via serial communication.

    This class handles communication with an Arduino that generates PWM signals
    for optical stimulation based on specified parameters for duration,
    intensity, and frequency.
    """

    def __init__(
        self,
        braid_folder: str,
        config_path: str = "config.toml",
        process_name: str = "OptoTrigger",
        log_level: str = "INFO",
        log_color: str = "RED",
    ):
        """
        Initialize the OptoTrigger controller.

        Args:
            config_path: Path to the configuration file
        """

        self.config = OptoTriggerConfig(config_path)
        self.serial_conn = None
        self.is_initialized = False

        # Initialize CSV writer
        if not os.path.exists(braid_folder):
            raise ValueError(f"braid folder does not exist: {braid_folder}")
        self.csv_writer = CSVWriter(os.path.join(braid_folder, "opto.csv"))

        # Initialize logger
        self.logger = init_class_logger(
            instance=self,
            process_name=process_name,
            log_level=log_level,
            log_color=log_color,
        )

        # Initialize logger
        self.logger.debug(f"OptoTrigger initialized with config: {self.config}")

    def initialize(self) -> bool:
        """
        Initialize the serial connection to the Arduino.

        Returns:
            True if connection was successful, False otherwise
        """
        try:
            self.logger.debug(f"Connecting to Arduino at {self.config.port}")
            self.serial_conn = serial.Serial(
                port=self.config.port,
                baudrate=self.config.baudrate,
                timeout=2,  # 2 second timeout
            )

            # Allow time for Arduino to reset after opening serial connection
            time.sleep(2)

            # Flush any pending data
            self.serial_conn.reset_input_buffer()
            self.serial_conn.reset_output_buffer()

            self.is_initialized = True
            self.logger.info(f"Connected to Arduino at {self.config.port}")
            return True
        except serial.SerialException as e:
            self.logger.error(f"Failed to initialize OptoTrigger: {e}")
            return False

    def trigger(
        self, obj_id: int, frame: int, timestamp: int, sham: Optional[bool] = None
    ) -> bool:
        """
        Send trigger command to the Arduino.

        Args:
            sham: Override sham setting. If None, uses probability from config.

        Returns:
            True if command was sent successfully, False otherwise
        """

        # Prepare the row for CSV logging
        row = {
            "obj_id": obj_id,
            "frame": frame,
            "timestamp": timestamp,
            "duration": self.config.duration,
            "intensity": self.config.intensity,
            "frequency": self.config.frequency,
            "color": self.config.color,
            "sham": False,  # Will be updated below
        }

        # Check if the trigger is initialized
        if not self.is_initialized:
            self.logger.error("OptoTrigger not initialized. Call initialize() first.")
            return False

        # Determine if this should be a sham stimulation
        if sham is None:
            sham = random.random() < self.config.sham_probability

        if sham:
            self.logger.info(
                f"Executing sham stimulation (no signal sent) for color {self.config.color}"
            )
            row["sham"] = True
            self.csv_writer.append(row)
            self.logger.debug(f"Sham stimulation row: {row}")
            return True

        try:
            # Get the command string from config
            command = self.config.get_trigger_command()
            self.logger.debug(f"Sending trigger command: {command}")

            # Send the command
            if self.serial_conn:
                self.serial_conn.write(command.encode("utf-8"))
            else:
                self.logger.error("Serial connection is not established.")
                return False

            # Log the command to CSV
            row["sham"] = False
            self.csv_writer.append(row)
            self.logger.debug(f"CSV row written: {row}")

            # Wait for response
            response = (
                self.serial_conn.readline().decode("utf-8", errors="ignore").strip()
            )
            if response:
                # Parse timing information if available
                self.logger.debug(f"Arduino response: {response}")
                if "Total processing time" in response:
                    timing_lines = []
                    for _ in range(4):  # Try to collect 4 lines of timing info
                        line = (
                            self.serial_conn.readline()
                            .decode("utf-8", errors="ignore")
                            .strip()
                        )
                        if line:
                            timing_lines.append(line)
                            self.logger.debug(line)

                # Log the completion of stimulation
                self.logger.info(
                    "Stimulation command processed "
                    f"({self.config.duration}ms, {self.config.intensity}/255, "
                    f"{self.config.frequency}Hz, color={self.config.color})"
                )
            else:
                self.logger.warning("No response received from Arduino")

            # Drain any remaining serial lines to keep buffer clean between triggers
            drained = 0
            while self.serial_conn and getattr(self.serial_conn, "in_waiting", 0):
                extra_line = (
                    self.serial_conn.readline().decode("utf-8", errors="ignore").strip()
                )
                if extra_line:
                    self.logger.debug(f"Discarding extra Arduino line: {extra_line}")
                drained += 1

            if drained:
                self.logger.debug(f"Serial buffer drain completed ({drained} line(s) discarded)")

            return True
        except Exception as e:
            self.logger.error(f"Error triggering stimulation: {e}")
            return False

    def close(self) -> bool:
        """
        Close the serial connection to the Arduino.

        Returns:
            True if closed successfully, False otherwise
        """
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
                self.is_initialized = False
                self.logger.info("OptoTrigger connection closed")
                return True
            except Exception as e:
                self.logger.error(f"Error closing OptoTrigger connection: {e}")
                return False
        return True

    def __enter__(self):
        """Support for context manager usage with 'with' statement."""
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure serial connection is closed when exiting context."""
        self.close()

    def set_parameters(
        self,
        duration: Optional[int] = None,
        intensity: Optional[int] = None,
        frequency: Optional[int] = None,
        color: Optional[str] = None,
    ) -> bool:
        """
        Update stimulation parameters.

        Args:
            duration: Duration in milliseconds (0-3000)
            intensity: PWM intensity (0-255)
            frequency: Frequency in Hz (0 for continuous)
            color: Color identifier for the RGB LED channels

        Returns:
            True if parameters were updated successfully
        """
        updated = False

        if duration is not None:
            self.config.duration = max(0, min(3000, int(duration)))
            updated = True

        if intensity is not None:
            self.config.intensity = max(0, min(255, int(intensity)))
            updated = True

        if frequency is not None:
            self.config.frequency = max(0, int(frequency))
            updated = True

        if color is not None:
            try:
                self.config.set_color(color)
                updated = True
            except ValueError as exc:
                self.logger.error(f"{exc}. Keeping color {self.config.color}.")

        if updated:
            self.logger.debug(
                "Parameters updated "
                f"({self.config.duration}ms, {self.config.intensity}/255, "
                f"{self.config.frequency}Hz, color={self.config.color})"
            )

        return updated


# Example usage when run directly
if __name__ == "__main__":
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="OptoTrigger Controller")
    parser.add_argument(
        "--config", "-c", default="config.toml", help="Path to config file"
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level",
    )
    parser.add_argument("--duration", "-d", type=int, help="Override duration (ms)")
    parser.add_argument(
        "--intensity", "-i", type=int, help="Override intensity (0-255)"
    )
    parser.add_argument("--frequency", "-f", type=int, help="Override frequency (Hz)")
    parser.add_argument(
        "--color",
        type=str,
        help=(
            "Override LED color (valid options: "
            + ", ".join(OptoTriggerConfig.valid_colors())
            + ")"
        ),
    )
    parser.add_argument(
        "--sham", "-s", action="store_true", help="Force sham stimulation"
    )
    parser.add_argument(
        "--obj-id",
        type=int,
        default=0,
        help="Object identifier to associate with the trigger event",
    )
    parser.add_argument(
        "--frame",
        type=int,
        default=0,
        help="Frame index to associate with the trigger event",
    )
    parser.add_argument(
        "--timestamp",
        type=int,
        help="Timestamp to log with the trigger (defaults to current time in ms)",
    )
    args = parser.parse_args()

    with OptoTrigger(
        braid_folder=".",
        config_path=args.config,
        log_level=args.log_level,
        process_name="OptoTriggerCLI",
    ) as trigger:
        # Apply any parameter overrides
        params_changed = False
        overrides = {
            "duration": args.duration,
            "intensity": args.intensity,
            "frequency": args.frequency,
            "color": args.color,
        }
        if any(value is not None for value in overrides.values()):
            params_changed = trigger.set_parameters(**overrides)

        # Log the parameters being used
        if params_changed:
            print(f"Using custom parameters: {trigger.config.get_trigger_command()}")

        # Trigger the stimulation
        trigger.trigger(
            obj_id=args.obj_id,
            frame=args.frame,
            timestamp=args.timestamp
            if args.timestamp is not None
            else int(time.time() * 1000),
            sham=args.sham if args.sham else None,
        )
