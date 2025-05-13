"""
OptoTrigger module for controlling Arduino-based optical stimulation.
"""

import random
import sys
import time
from typing import Optional

import serial
from loguru import logger

from config import OptoTriggerConfig

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
)


class OptoTrigger:
    """
    Controls an Arduino-based optical stimulation system via serial communication.

    This class handles communication with an Arduino that generates PWM signals
    for optical stimulation based on specified parameters for duration,
    intensity, and frequency.
    """

    def __init__(self, config_path: str = "config.toml"):
        """
        Initialize the OptoTrigger controller.

        Args:
            config_path: Path to the configuration file
        """
        self.config = OptoTriggerConfig(config_path)
        self.serial_conn = None
        self.is_initialized = False
        logger.debug(f"OptoTrigger initialized with config: {self.config}")

    def initialize(self) -> bool:
        """
        Initialize the serial connection to the Arduino.

        Returns:
            True if connection was successful, False otherwise
        """
        try:
            logger.debug(f"Connecting to Arduino at {self.config.port}")
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
            logger.info(f"Connected to Arduino at {self.config.port}")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to initialize OptoTrigger: {e}")
            return False

    def trigger(self, sham: Optional[bool] = None) -> bool:
        """
        Send trigger command to the Arduino.

        Args:
            sham: Override sham setting. If None, uses probability from config.

        Returns:
            True if command was sent successfully, False otherwise
        """
        if not self.is_initialized:
            logger.error("OptoTrigger not initialized. Call initialize() first.")
            return False

        # Determine if this should be a sham stimulation
        if sham is None:
            sham = random.random() < self.config.sham_probability

        if sham:
            logger.info("Executing sham stimulation (no signal sent)")
            return True

        try:
            # Get the command string from config
            command = self.config.get_trigger_command()
            logger.debug(f"Sending trigger command: {command}")

            # Send the command
            if self.serial_conn:
                self.serial_conn.write(command.encode("utf-8"))
            else:
                logger.error("Serial connection is not established.")
                return False

            # Wait for response
            response = (
                self.serial_conn.readline().decode("utf-8", errors="ignore").strip()
            )
            if response:
                # Parse timing information if available
                logger.debug(f"Arduino response: {response}")
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
                            logger.debug(line)

                # Log the completion of stimulation
                logger.info(
                    f"Stimulation command processed ({self.config.duration}ms, {self.config.intensity}/255, {self.config.frequency}Hz)"
                )
            else:
                logger.warning("No response received from Arduino")

            return True
        except Exception as e:
            logger.error(f"Error triggering stimulation: {e}")
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
                logger.info("OptoTrigger connection closed")
                return True
            except Exception as e:
                logger.error(f"Error closing OptoTrigger connection: {e}")
                return False
        return True

    def __enter__(self):
        """Support for context manager usage with 'with' statement."""
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure serial connection is closed when exiting context."""
        self.close()

    def set_parameters(self, duration=None, intensity=None, frequency=None) -> bool:
        """
        Update stimulation parameters.

        Args:
            duration: Duration in milliseconds (0-3000)
            intensity: PWM intensity (0-255)
            frequency: Frequency in Hz (0 for continuous)

        Returns:
            True if parameters were updated successfully
        """
        if duration is not None:
            self.config.duration = max(0, min(3000, int(duration)))

        if intensity is not None:
            self.config.intensity = max(0, min(255, int(intensity)))

        if frequency is not None:
            self.config.frequency = max(0, int(frequency))

        logger.debug(f"Parameters updated: {self.config.get_trigger_command()}")
        return True


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
        "--sham", "-s", action="store_true", help="Force sham stimulation"
    )
    args = parser.parse_args()

    # Run the trigger
    with OptoTrigger(args.config) as trigger:
        # Apply any parameter overrides
        params_changed = False
        if (
            args.duration is not None
            or args.intensity is not None
            or args.frequency is not None
        ):
            trigger.set_parameters(
                duration=args.duration,
                intensity=args.intensity,
                frequency=args.frequency,
            )
            params_changed = True

        # Log the parameters being used
        if params_changed:
            logger.info(
                f"Using custom parameters: {trigger.config.get_trigger_command()}"
            )

        # Trigger the stimulation
        trigger.trigger(sham=args.sham if args.sham else None)
