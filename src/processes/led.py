"""
OptoTrigger Worker Process for ZMQ-based optogenetic stimulation control.

This process subscribes to TRIGGER messages from the TriggerHandler,
controls the OptoTrigger hardware, and logs all trigger events to CSV.
"""

import multiprocessing as mp
import os
import time
import json
from typing import Dict, Optional

import zmq

from src.utils.config import OptoTriggerConfig, ZMQConfig
from src.utils.worker import WorkerProcess
from src.hardware.led import OptoTrigger
from src.utils.csv_writer import CSVWriter


class OptoTriggerWorker(WorkerProcess):
    def __init__(
        self,
        event: mp.Event,
        braid_folder: str,
        config_path: str = "config.toml",
        process_name: str = "OptoTriggerWorker",
        log_level: str = "INFO",
        log_color: str = "RED",
    ):
        """
        Initialize the OptoTriggerWorker process.

        Args:
            event: Event to signal process termination
            braid_folder: Path to folder for CSV logging
            config_path: Path to the configuration file
            process_name: Name to display in logs
            log_level: Logging level to use
            log_color: Color for log messages
        """
        if event is None:
            raise ValueError("OptoTriggerWorker requires an external stop event.")

        # Pass parameters to parent class
        super().__init__(
            event=event,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        # Validate braid_folder
        if not os.path.exists(braid_folder):
            raise ValueError(f"braid folder does not exist: {braid_folder}")
        self.braid_folder = braid_folder

        # Store configuration path
        self.config_path = config_path

        # Initialize configurations
        self.opto_config = OptoTriggerConfig(config_path)
        self.zmq_config = ZMQConfig(config_path)

        # Process state
        self.stop_event = event
        self.is_running = False
        self.opto_trigger = None
        self.csv_writer = None
        self.trigger_socket = None
        self.context = None

        # Check if it's enabled
        self.is_enabled = self.opto_config.active
        if not self.is_enabled:
            # Don't initialize logger yet - will be done in run()
            pass

    def initialize(self):
        """
        Initialize the process components.
        """
        # Initialize logger (must be done in the child process)
        self._initialize_logger()

        self.logger.debug(f"OptoTrigger config: {self.opto_config}")

        # Initialize ZMQ socket
        self._initialize_zmq()

        # Initialize CSV writer
        self._initialize_csv_writer()

        # Initialize OptoTrigger hardware
        self._initialize_hardware()

        self.logger.info("OptoTriggerWorker process initialized.")

    def _initialize_zmq(self):
        """Initialize ZMQ socket for receiving TRIGGER messages."""
        try:
            self.context = zmq.Context()
            self.trigger_socket = self.context.socket(zmq.SUB)
            self.trigger_socket.connect(
                self.zmq_config.get_subscriber_address(self.zmq_config.trigger_port)
            )
            self.trigger_socket.setsockopt_string(
                zmq.SUBSCRIBE, self.zmq_config.trigger_topic
            )
            self.logger.debug(
                f"Connected to TriggerHandler on port {self.zmq_config.trigger_port}"
            )
        except Exception as e:
            self.logger.error(f"Error connecting to ZMQ socket: {e}")
            raise

    def _initialize_csv_writer(self):
        """Initialize CSV writer for logging trigger events."""
        try:
            csv_path = os.path.join(self.braid_folder, "opto.csv")
            self.csv_writer = CSVWriter(csv_path)
            self.logger.debug(f"CSV writer initialized at {csv_path}")
        except Exception as e:
            self.logger.error(f"Error initializing CSV writer: {e}")
            raise

    def _initialize_hardware(self):
        """Initialize OptoTrigger hardware controller."""
        try:
            self.opto_trigger = OptoTrigger(
                config_path=self.config_path,
                process_name=self.process_name,
                log_level=self.log_level,
                log_color=self.log_color,
            )
            success = self.opto_trigger.initialize()
            if not success:
                self.logger.warning(
                    "OptoTrigger hardware initialization failed. "
                    "Will continue but triggers may not work."
                )
        except Exception as e:
            self.logger.error(f"Error initializing OptoTrigger hardware: {e}")
            raise

    def _receive_message(self) -> Optional[Dict]:
        """
        Receive a TRIGGER message from ZMQ socket.

        Returns:
            The parsed message dictionary or None if no message available
        """
        try:
            message = self.trigger_socket.recv_string(flags=zmq.NOBLOCK)
            # Message format: "TRIGGER {json_data}"
            _, json_data = message.split(" ", 1)
            return json.loads(json_data)
        except zmq.Again:
            return None
        except Exception as e:
            self.logger.error(f"Error receiving TRIGGER message: {e}")
            return None

    def _handle_trigger(self, trigger_data: Dict) -> bool:
        """
        Handle a trigger event by activating hardware and logging to CSV.

        Only activates LED for 'stimulation' type triggers.
        'recording' type triggers are ignored by this process.

        Args:
            trigger_data: Dictionary containing obj_id, frame, timestamps, mean_heading, trigger_type, etc.

        Returns:
            True if trigger was handled successfully
        """
        try:
            # Extract trigger information
            obj_id = trigger_data.get("obj_id")
            frame = trigger_data.get("frame")
            trigger_type = trigger_data.get("trigger_type", "stimulation")  # Default for backward compatibility

            # Get both timestamps (with backward compatibility fallback)
            braid_timestamp = trigger_data.get("braid_timestamp")
            trigger_timestamp = trigger_data.get("trigger_timestamp")

            # Fallback to old 'timestamp' field if new fields not present
            if braid_timestamp is None:
                braid_timestamp = trigger_data.get("timestamp")
            if trigger_timestamp is None:
                trigger_timestamp = trigger_data.get("timestamp")

            mean_heading = trigger_data.get("mean_heading")

            if obj_id is None or frame is None:
                self.logger.warning(
                    f"Incomplete trigger data received: {trigger_data}"
                )
                return False

            # Only activate LED for stimulation triggers
            if trigger_type != "stimulation":
                self.logger.debug(
                    f"Ignoring trigger_type='{trigger_type}' for object {obj_id} "
                    f"(OptoTrigger only responds to 'stimulation')"
                )
                return True  # Not an error, just not our trigger type

            self.logger.info(
                f"Received STIMULATION trigger for object {obj_id} on frame {frame} "
                f"(heading={mean_heading})"
            )

            # Trigger the hardware (it will determine sham based on probability)
            success, was_sham = self.opto_trigger.trigger(sham=None)

            # Prepare CSV row
            # Use values from the hardware controller (which has the randomly selected parameters)
            row = {
                "obj_id": obj_id,
                "frame": frame,
                "braid_timestamp": braid_timestamp,
                "trigger_timestamp": trigger_timestamp,
                "mean_heading": mean_heading,
                "trigger_type": trigger_type,
                "duration": self.opto_trigger.config.duration,
                "intensity": self.opto_trigger.config.intensity,
                "frequency": self.opto_trigger.config.frequency,
                "color": self.opto_trigger.config.color,
                "sham": was_sham,
            }

            # Log to CSV
            self.csv_writer.append(row)
            self.logger.debug(f"Logged trigger event to CSV: {row}")

            return success

        except Exception as e:
            self.logger.error(f"Error handling trigger: {e}")
            return False

    def run(self):
        """Main process loop."""
        # Check if enabled
        if not self.is_enabled:
            # Initialize logger even if disabled, so we can log the warning
            self._initialize_logger()
            self.logger.warning("OptoTriggerWorker is disabled. Exiting.")
            return

        try:
            self.initialize()
        except Exception as e:
            # Logger should be initialized by now
            if self.logger:
                self.logger.error(f"OptoTriggerWorker failed to initialize: {e}")
            return

        self.is_running = True
        self.logger.info("OptoTriggerWorker process started.")

        try:
            while self.is_running and not self.stop_event.is_set():
                try:
                    # Check for TRIGGER messages
                    trigger_data = self._receive_message()

                    if trigger_data is not None:
                        self._handle_trigger(trigger_data)

                    # Small sleep to avoid busy waiting
                    time.sleep(0.001)

                except Exception as e:
                    self.logger.error(f"Error in OptoTriggerWorker main loop: {e}")
                    # Continue running despite errors
        except KeyboardInterrupt:
            pass  # Graceful shutdown via stop_event

        self.logger.info("OptoTriggerWorker process stopped.")

        # Cleanup
        self.close()
        self.logger.info("OptoTriggerWorker process closed.")

    def close(self):
        """Close all resources and connections."""
        self.is_running = False

        # Close OptoTrigger hardware
        if self.opto_trigger:
            try:
                self.opto_trigger.close()
                self.logger.debug("OptoTrigger hardware closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing OptoTrigger hardware: {e}")

        # Close CSV writer
        if self.csv_writer:
            try:
                self.csv_writer.close()
                self.logger.debug("CSV writer closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing CSV writer: {e}")

        # Close ZMQ socket
        if self.trigger_socket:
            try:
                self.trigger_socket.close()
                self.logger.debug("Trigger socket closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing trigger socket: {e}")

        # Terminate ZMQ context
        if self.context:
            try:
                self.context.term()
                self.logger.debug("ZMQ context terminated successfully")
            except Exception as e:
                self.logger.error(f"Error terminating ZMQ context: {e}")


# Allow running as standalone module for testing
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OptoTrigger Worker Process")
    parser.add_argument(
        "--config", "-c", default="config.toml", help="Path to config file"
    )
    parser.add_argument(
        "--braid-folder",
        "-b",
        default=".",
        help="Path to braid folder for CSV logging",
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level",
    )
    args = parser.parse_args()

    # Create stop event
    stop_event = mp.Event()

    # Create and start worker
    worker = OptoTriggerWorker(
        event=stop_event,
        braid_folder=args.braid_folder,
        config_path=args.config,
        log_level=args.log_level,
    )

    try:
        worker.start()
        worker.join()
    except KeyboardInterrupt:
        print("\nStopping OptoTriggerWorker...")
        stop_event.set()
        worker.join(timeout=5)
