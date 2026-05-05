"""
OptoTrigger module for controlling Arduino-based optical stimulation.

This module provides a pure hardware controller for Arduino-based optogenetic
stimulation. It handles serial communication and command generation only.
Data logging and process orchestration should be handled by the worker process.
"""

import itertools
import random
import time
from typing import Optional

import numpy as np
import serial

import logging

from src.utils.config import OptoTriggerConfig


class OptoTrigger:
    """
    Controls an Arduino-based optical stimulation system via serial communication.

    This class handles communication with an Arduino that generates PWM signals
    for optical stimulation based on specified parameters for duration,
    intensity, and frequency.

    **Balanced Randomization:**
    Stimulation parameters support balanced randomization to ensure uniform
    sampling across all parameter combinations throughout the experimental session.

    Each parameter can be specified in configs/config.toml as either:
    - Single value: Used for every stimulation (e.g., frequency = 0)
    - List of options: Balanced selection across combinations (e.g., duration = [100, 200, 300])

    The system tracks usage counts for all parameter combinations and preferentially
    selects least-used combinations, guaranteeing balanced presentation:
    - Maximum variance of 1 between any two combinations
    - Prevents clustering bias from pure random selection
    - Counts persist throughout the experimental session

    **Randomizable Parameters:**
    - duration: Stimulation duration in milliseconds (0-3000)
    - intensity: PWM intensity (0-255)
    - frequency: Frequency in Hz (0 for continuous)

    **Example Configuration:**
    ```toml
    [opto_trigger]
    active = true
    duration = [100, 200, 300]     # 3 options
    intensity = [0, 51, 102, 153, 204, 255]  # 6 options
    frequency = 0                  # Fixed (or use list: [0, 10, 20, 50])
    ```

    With the above config (3 durations × 6 intensities × 1 frequency = 18 combinations):
    - After 120 trials: each combination appears exactly 6-7 times
    - Guarantees uniform experimental design

    All selected parameter values are logged for full reproducibility.
    """

    def __init__(
        self,
        config_path: str = "configs/config.toml",
        process_name: str = "OptoTrigger",
        log_level: str = "INFO",
        log_color: str = "RED",
    ):
        """
        Initialize the OptoTrigger hardware controller.

        Args:
            config_path: Path to the configuration file
            process_name: Name for logging purposes
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
            log_color: Color for log messages
        """

        self.config = OptoTriggerConfig(config_path)
        self.serial_conn = None
        self.is_initialized = False

        self.logger = logging.getLogger(__name__)

        # PARAMETER COMBINATION BALANCING
        # Generate all possible parameter combinations for balanced selection
        self._parameter_combinations = self._generate_combinations()

        # Track usage counts for each combination (balanced presentation)
        # Initialized to zero, persists throughout experimental session
        self.combination_counts = {combo: 0 for combo in self._parameter_combinations}

        self.logger.debug(f"OptoTrigger initialized with config: {self.config}")
        self.logger.debug(
            f"Initialized balanced randomization with "
            f"{len(self._parameter_combinations)} parameter combinations "
            f"({len(self.config.duration_options)} durations × "
            f"{len(self.config.intensity_options)} intensities × "
            f"{len(self.config.frequency_options)} frequencies)"
        )

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

    def trigger(self, sham: Optional[bool] = None) -> tuple[bool, bool]:
        """
        Send trigger command to the Arduino.

        Args:
            sham: Override sham setting. If None, uses probability from config.

        Returns:
            Tuple of (success, was_sham):
                - success: True if command was sent/processed successfully
                - was_sham: True if this was a sham stimulation (no signal sent)
        """

        # Check if the trigger is initialized
        if not self.is_initialized:
            self.logger.error("OptoTrigger not initialized. Call initialize() first.")
            return (False, False)

        # Determine if this should be a sham stimulation
        if sham is None:
            sham = random.random() < self.config.sham_probability

        if sham:
            self.logger.info(
                f"Executing sham stimulation (no signal sent) for color {self.config.color}"
            )
            return (True, True)

        # Select parameters using balanced randomization
        params = self._select_balanced_parameters()
        self.set_parameters(
            duration=params["duration"],
            intensity=params["intensity"],
            frequency=params["frequency"],
        )

        try:
            # Get the command string from config
            command = self.config.get_trigger_command()
            self.logger.debug(f"Sending trigger command: {command}")

            # Send the command
            if self.serial_conn:
                # Use \r\n line ending to match Arduino IDE serial monitor behavior
                self.serial_conn.write((command + "\r\n").encode("utf-8"))
                self.serial_conn.flush()  # Ensure data is sent immediately
            else:
                self.logger.error("Serial connection is not established.")
                return (False, False)

            # Wait for Arduino output without blocking on readline timeouts
            response_lines = self._collect_serial_output()

            if response_lines:
                primary = response_lines[0]
                self.logger.info(
                    "Stimulation command processed "
                    f"({self.config.duration}ms, {self.config.intensity}/255, "
                    f"{self.config.frequency}Hz, color={self.config.color})"
                )
                self.logger.debug(f"Arduino response: {primary}")

                if len(response_lines) > 1:
                    diagnostics = "; ".join(response_lines[1:])
                    self.logger.debug(f"Arduino diagnostics: {diagnostics}")
            else:
                self.logger.warning("No response received from Arduino")

            return (True, False)
        except Exception as e:
            self.logger.error(f"Error triggering stimulation: {e}")
            return (False, False)

    def _generate_combinations(self) -> list[tuple[int, ...]]:
        """Generate all possible parameter combinations for balanced selection.

        Creates Cartesian product of all parameter option lists:
        - duration_options × intensity_options × frequency_options

        Handles N-way combinations automatically using itertools.product().
        If a parameter has only 1 option, it's still included in the combination
        but doesn't increase the total number of combinations.

        Returns:
            List of tuples, where each tuple is (duration, intensity, frequency)

        Examples:
            duration=[100, 200], intensity=[0, 255], frequency=[0]
            → [(100, 0, 0), (100, 255, 0), (200, 0, 0), (200, 255, 0)]

            duration=[100, 200], intensity=[0, 255], frequency=[0, 10]
            → [(100, 0, 0), (100, 0, 10), (100, 255, 0), (100, 255, 10),
               (200, 0, 0), (200, 0, 10), (200, 255, 0), (200, 255, 10)]
        """
        combinations = list(
            itertools.product(
                self.config.duration_options,
                self.config.intensity_options,
                self.config.frequency_options,
            )
        )

        # Convert to tuple of ints for consistent dictionary keys
        return [tuple(int(val) for val in combo) for combo in combinations]

    def _select_balanced_parameters(self) -> dict:
        """Select parameter combination with least usage (balanced selection).

        Implements balanced presentation algorithm (same as visual stimuli):
        1. Find minimum usage count across all combinations
        2. Get all combinations with that count (candidates)
        3. Randomly select from candidates (breaks ties randomly)
        4. Increment selected combination's count
        5. Return selected parameters

        This ensures uniform sampling across all parameter combinations over
        the experimental session, preventing clustering bias that occurs with
        pure random selection.

        With N combinations and M trials:
        - Each combination appears floor(M/N) or ceil(M/N) times
        - Maximum variance between any two combinations is 1

        Example:
            18 combinations, 120 trials → each appears 6-7 times
            72 combinations, 120 trials → each appears 1-2 times

        Returns:
            Dictionary with keys: "duration", "intensity", "frequency"

        Example:
            combination_counts = {
                (100, 0, 0): 5,
                (100, 51, 0): 3,
                (200, 0, 0): 3,
                ...
            }
            → min_count = 3
            → candidates = [(100, 51, 0), (200, 0, 0), ...]
            → randomly select (200, 0, 0)
            → increment: combination_counts[(200, 0, 0)] = 4
            → return {"duration": 200, "intensity": 0, "frequency": 0}
        """
        # Find minimum usage count across all combinations
        min_count = min(self.combination_counts.values())

        # Get all combinations with minimum count (candidates for selection)
        candidates = [
            combo
            for combo, count in self.combination_counts.items()
            if count == min_count
        ]

        # Randomly select from candidates (breaks ties randomly)
        selected = candidates[np.random.randint(len(candidates))]

        # Increment usage count for selected combination
        self.combination_counts[selected] += 1

        # Unpack selected combination
        duration, intensity, frequency = selected

        # Log selection with debug level
        self.logger.debug(
            f"Selected balanced parameters: duration={duration}ms, "
            f"intensity={intensity}/255, frequency={frequency}Hz "
            f"(count: {self.combination_counts[selected]}/{min_count + 1}, "
            f"candidates: {len(candidates)}/{len(self._parameter_combinations)})"
        )

        return {
            "duration": int(duration),
            "intensity": int(intensity),
            "frequency": int(frequency),
        }

    def set_backlight(self, intensity: int) -> bool:
        """
        Set the backlight intensity via the [intensity] command.

        Args:
            intensity: 0 (off) to 255 (full brightness)

        Returns:
            True if command was sent successfully
        """
        if not self.is_initialized or not self.serial_conn:
            self.logger.error("OptoTrigger not initialized. Call initialize() first.")
            return False
        try:
            intensity = max(0, min(255, int(intensity)))
            command = f"[{intensity}]\r\n"
            self.serial_conn.write(command.encode("utf-8"))
            self.serial_conn.flush()
            self.logger.debug(f"Backlight set to {intensity}")
            return True
        except Exception as e:
            self.logger.error(f"Error setting backlight: {e}")
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
            except Exception as exc:
                self.logger.error(f"Error closing OptoTrigger connection: {exc}")
                return False
        return True

    def __enter__(self):
        """Support for context manager usage with 'with' statement."""
        self.initialize()
        return self

    def _collect_serial_output(
        self, timeout: float = 1.0, poll_interval: float = 0.05
    ) -> list[str]:
        """Return non-empty lines read from the serial buffer.

        This drains available bytes without blocking on serial timeouts and
        stops once no new data arrives within the poll interval or the timeout
        is exceeded.
        """

        if not self.serial_conn:
            return []

        buffer = bytearray()
        start = time.time()

        while time.time() - start < timeout:
            waiting = getattr(self.serial_conn, "in_waiting", 0)
            if waiting:
                chunk = self.serial_conn.read(waiting)
                if chunk:
                    buffer.extend(chunk)
                    start = time.time()
                    continue
            time.sleep(poll_interval)

        if not buffer:
            return []

        decoded = buffer.decode("utf-8", errors="ignore")
        return [line.strip() for line in decoded.splitlines() if line.strip()]

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
        "--config", "-c", default="configs/config.toml", help="Path to config file"
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
    args = parser.parse_args()

    with OptoTrigger(
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
        success, was_sham = trigger.trigger(sham=args.sham if args.sham else None)

        if success:
            if was_sham:
                print("Sham stimulation executed successfully (no signal sent)")
            else:
                print("Stimulation executed successfully")
        else:
            print("Stimulation failed")
