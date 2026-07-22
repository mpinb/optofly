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
from src.utils.trigger_timing import TriggerTiming, extract_trigger_timing


class OptoTriggerWorker(WorkerProcess):
    def __init__(
        self,
        event: mp.Event,
        braid_folder: str,
        config_path: str = "configs/config.toml",
        process_name: str = "OptoTriggerWorker",
        log_level: str = "INFO",
        log_color: str = "RED",
        log_path: str | None = None,
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
            log_path: Path to shared log file (written from child process)
        """
        if event is None:
            raise ValueError("OptoTriggerWorker requires an external stop event.")

        # Pass parameters to parent class
        super().__init__(
            event=event,
            log_path=log_path,
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
        self.latency_socket = None
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
        self.logger.debug(f"OptoTrigger config: {self.opto_config}")

        # Initialize OptoTrigger hardware first (needed for backlight regardless of active flag)
        self._initialize_hardware()

        # Turn on backlight
        if self.opto_trigger:
            self.opto_trigger.set_backlight(255)

        if self.is_enabled:
            # Initialize ZMQ socket only if trigger stimulation is enabled
            self._initialize_zmq()

            # Initialize CSV writer
            self._initialize_csv_writer()

        self.logger.info("OptoTriggerWorker process initialized.")

    def _initialize_zmq(self):
        """Initialize ZMQ sockets: SUB for ZONE_ENTER, PUSH for LATENCY."""
        try:
            self.context = zmq.Context()
            self.trigger_socket = self.context.socket(zmq.SUB)
            self.trigger_socket.connect(
                self.zmq_config.get_subscriber_address(self.zmq_config.trigger_port)
            )
            self.trigger_socket.setsockopt_string(
                zmq.SUBSCRIBE, self.zmq_config.zone_enter_topic
            )
            self.logger.debug(
                f"Connected to TriggerHandler on port {self.zmq_config.trigger_port} "
                f"(topic: {self.zmq_config.zone_enter_topic})"
            )

            # LATENCY reporting: PUSH connects to LatencyLogger's bound PULL
            # socket (many-producer, one-consumer fan-in).
            self.latency_socket = self.context.socket(zmq.PUSH)
            self.latency_socket.connect(
                self.zmq_config.get_subscriber_address(self.zmq_config.latency_port)
            )
            self.logger.debug(
                f"Connected LATENCY publisher to port {self.zmq_config.latency_port}"
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
                self.logger.error(
                    "OptoTrigger hardware initialization FAILED. "
                    f"Cannot open serial port — check opto_trigger.port in config. "
                    "Backlight will not turn on. Aborting."
                )
                raise RuntimeError("OptoTrigger hardware initialization failed")
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
            topic, message = self.trigger_socket.recv_multipart(flags=zmq.NOBLOCK)
            topic = topic.decode("utf-8")
            json_data = message.decode("utf-8")
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
            timing = extract_trigger_timing(trigger_data)
            mean_heading = trigger_data.get("mean_heading")

            if obj_id is None or frame is None:
                self.logger.warning(f"Incomplete trigger data received: {trigger_data}")
                return False

            self.logger.info(
                f"Received trigger for object {obj_id} on frame {frame} "
                f"(heading={mean_heading})"
            )

            # Trigger the hardware (it will determine sham based on probability)
            success, was_sham, activation_timestamp = self.opto_trigger.trigger(
                sham=None
            )

            # Prepare CSV row
            # Use values from the hardware controller (which has the randomly selected parameters)
            row = {
                "obj_id": obj_id,
                "frame": frame,
                "braid_timestamp": timing.braid_timestamp,
                "trigger_timestamp": timing.handler_timestamp,
                "mean_heading": mean_heading,
                "duration": self.opto_trigger.config.duration,
                "intensity": self.opto_trigger.config.intensity,
                "frequency": self.opto_trigger.config.frequency,
                "color": self.opto_trigger.config.color,
                "sham": was_sham,
            }

            # Log to CSV
            self.csv_writer.append(row)
            self.logger.debug(f"Logged trigger event to CSV: {row}")

            self._publish_latency(obj_id, frame, timing, activation_timestamp, was_sham)

            return success

        except Exception as e:
            self.logger.error(f"Error handling trigger: {e}")
            return False

    def _publish_latency(
        self,
        obj_id,
        frame,
        timing: TriggerTiming,
        activation_timestamp,
        sham: bool,
    ) -> None:
        """Publish one LATENCY message for the methods-paper latency log."""
        try:
            message = {
                "system": "opto",
                "obj_id": obj_id,
                "frame": frame,
                "braid_timestamp": timing.braid_timestamp,
                "trigger_timestamp": timing.handler_timestamp,
                "activation_timestamp": activation_timestamp,
                "sham": sham,
            }
            self.latency_socket.send(json.dumps(message).encode("utf-8"))
        except Exception as e:
            self.logger.error(f"Error publishing LATENCY message: {e}")

    def _run(self):
        """Main process loop."""
        try:
            self.initialize()
        except Exception as e:
            if self.logger:
                self.logger.error(f"OptoTriggerWorker failed to initialize: {e}")
            return

        self.is_running = True
        if self.is_enabled:
            self.logger.info("OptoTriggerWorker process started (stimulation active).")
        else:
            self.logger.info(
                "OptoTriggerWorker process started (backlight only, stimulation disabled)."
            )

        try:
            while self.is_running and not self.stop_event.is_set():
                try:
                    if self.is_enabled:
                        trigger_data = self._receive_message()
                        if trigger_data is not None:
                            self._handle_trigger(trigger_data)

                    time.sleep(0.001)

                except Exception as e:
                    self.logger.error(f"Error in OptoTriggerWorker main loop: {e}")
        except KeyboardInterrupt:
            pass  # Graceful shutdown via stop_event

        self.logger.info("OptoTriggerWorker process stopped.")

        # Cleanup
        self.close()
        self.logger.info("OptoTriggerWorker process closed.")

    def close(self):
        """Close all resources and connections."""
        self.is_running = False

        # Turn off backlight before closing
        if self.opto_trigger:
            self.opto_trigger.set_backlight(0)

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

        # Close ZMQ sockets
        for sock_attr in ("trigger_socket", "latency_socket"):
            sock = getattr(self, sock_attr, None)
            if sock:
                try:
                    sock.close()
                    self.logger.debug(f"{sock_attr} closed successfully")
                except Exception as e:
                    self.logger.error(f"Error closing {sock_attr}: {e}")

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
        "--config", "-c", default="configs/config.toml", help="Path to config file"
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
