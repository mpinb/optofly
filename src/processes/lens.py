import multiprocessing as mp
import os
from typing import Optional, Dict, Tuple, Literal

import time
import numpy as np
import pandas as pd
import zmq
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures
import json

from src.utils.config import LiquidLensConfig, ZMQConfig, CameraConfig
from src.utils.csv_writer import CSVWriter
from src.utils.worker import WorkerProcess
from src.utils.kalman_filter import KalmanFilter
from src.hardware.lens import LensDriver


class LensCalibration:
    """
    Calibration utility for mapping z position to lens diopter values.

    This class handles the conversion between z positions and corresponding
    lens diopter settings using polynomial regression.
    """

    def __init__(self, z_values, dpt_values, n_elements=1000):
        """
        Initialize the LensCalibration class.

        Args:
            z_values: Array of z position values
            dpt_values: Array of corresponding diopter values
            n_elements: Number of elements in the lookup table
        """
        self.z_values = np.array(z_values)
        self.dpt_values = np.array(dpt_values)
        self.n_elements = n_elements
        self.create_lookup_table()

    def create_lookup_table(self):
        """Create a lookup table for diopter values using polynomial regression."""
        # Generate evenly spaced z values for the lookup table
        self.z_table = np.linspace(
            self.z_values.min(), self.z_values.max(), self.n_elements
        )

        # Interpolate dpt values for the lookup table using polynomial regression
        self.model = make_pipeline(PolynomialFeatures(2), LinearRegression())
        self.model.fit(self.z_values.reshape(-1, 1), self.dpt_values)
        self.dpt_table = self.model.predict(self.z_table.reshape(-1, 1))

    def get_dpt(self, z: float) -> float:
        """
        Get the diopter value for a given z position.

        Args:
            z: The z position

        Returns:
            The corresponding diopter value from the lookup table
        """
        z = np.asarray(z)
        # Find the index of the closest z value in the lookup table
        idx = np.abs(self.z_table - z).argmin()
        return self.dpt_table[idx]


def setup_lens_calibration(calibration_file: str, n_elements=1000) -> LensCalibration:
    """
    Set up the lens calibration model from a CSV file.

    Args:
        calibration_file: Path to the CSV file containing calibration data
        n_elements: Number of elements in the lookup table

    Returns:
        A LensCalibration object

    Raises:
        Exception: If calibration data cannot be loaded or processed
    """
    try:
        calibration_data = pd.read_csv(calibration_file)
        z_values = calibration_data["z"].values
        dpt_values = calibration_data["dpt"].values
        return LensCalibration(z_values, dpt_values, n_elements)
    except Exception as e:
        raise RuntimeError(f"Error setting up lens calibration: {e}")


class LiquidLens(WorkerProcess):
    def __init__(
        self,
        event: mp.Event,
        config_path: str = "configs/config.toml",
        braid_folder: Optional[str] = None,
        process_name: str = "LiquidLens",
        log_level: str = "INFO",
        log_color: str = "GREEN",
    ):
        """
        Initialize the LiquidLens process.

        Args:
            event: Event to signal process termination
            config_path: Path to the configuration file
            braid_folder: Path to braid experiment folder for CSV logging
            process_name: Name to display in logs
            log_level: Logging level to use
            log_color: Color for log messages
        """
        if event is None:
            raise ValueError("LiquidLens requires an external stop event.")
        super().__init__(
            event=event,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        self._initialize_logger()

        self.lens_config = LiquidLensConfig(config_path)
        self.zmq_config = ZMQConfig(config_path)
        self.camera_config = CameraConfig(config_path)
        self.stop_event = event
        self.is_running = False
        self.is_tracking = False
        self.current_tracked_obj = None
        self.braid_folder = braid_folder
        self.csv_writer = None
        self.kalman_filters = {}

    def initialize(self):
        """
        Initialize the process.
        """

        self.logger.debug(f"Liquid Lens config: {self.lens_config}")

        # initialize the ZMQ sockets
        self._initialize_zmq()

        # initialize the liquid lens calibration model
        self._initialize_calibration_model()

        # initialize the lens
        self._initialize_lens()

        # initialize CSV writer for debug logging
        self._initialize_csv()

        # Log the Kalman filter configuration
        if self.lens_config.kalman_enabled:
            self.logger.info(
                f"Kalman filter enabled with process_noise={self.lens_config.process_noise}, "
                f"measurement_noise={self.lens_config.measurement_noise}, "
                f"prediction_horizon={self.lens_config.prediction_horizon}s"
            )
        else:
            self.logger.info("Kalman filter is disabled")

        self.logger.info("Liquid Lens process initialized.")

    def _initialize_lens(self):
        try:
            self.lens_driver = LensDriver(port=self.lens_config.port)

            if self.lens_config.mode == "diopter":
                self.lens_driver.to_focal_power_mode()
            elif self.lens_config.mode == "current":
                self.lens_driver.to_current_mode()
            else:
                raise ValueError(f"Invalid lens mode: {self.lens_config.mode}")
        except Exception as e:
            self.logger.error(f"Error initializing lens driver: {e}")
            raise

    def _initialize_csv(self):
        """Initialize CSV writer for lens debug logging."""
        if self.braid_folder:
            csv_path = os.path.join(self.braid_folder, "liquid_lens.csv")
        else:
            csv_path = "liquid_lens.csv"
        self.csv_writer = CSVWriter(csv_path, strict=False)
        self.logger.info(f"CSV logging to: {csv_path}")

    def _log_csv(self, event: str, **kwargs):
        """Append a row to the lens CSV log."""
        if self.csv_writer is None:
            return
        row = {"timestamp": time.time(), "event": event}
        row.update(kwargs)
        self.csv_writer.append(row)

    def _initialize_calibration_model(self):
        """
        Initialize the lens calibration model.
        """
        try:
            self.lens_calibration = setup_lens_calibration(
                self.lens_config.calibration_file,
                n_elements=self.lens_config.n_elements,
            )
            self.logger.debug("Lens calibration model initialized successfully.")
        except Exception as e:
            self.logger.error(f"Error setting up lens calibration: {e}")
            raise

    def _initialize_zmq(self):
        # Connect to the BraidPublisher
        try:
            self.context = zmq.Context()
            self.braid_socket = self.context.socket(zmq.SUB)
            self.braid_socket.connect(
                self.zmq_config.get_subscriber_address(self.zmq_config.braid_port)
            )
            self.braid_socket.setsockopt_string(
                zmq.SUBSCRIBE, self.zmq_config.braid_topic
            )

            # Connect to the TriggerHandler for zone events
            self.trigger_socket = self.context.socket(zmq.SUB)
            self.trigger_socket.connect(
                self.zmq_config.get_subscriber_address(self.zmq_config.trigger_port)
            )
            self.trigger_socket.setsockopt_string(
                zmq.SUBSCRIBE, self.zmq_config.zone_enter_topic
            )
            self.trigger_socket.setsockopt_string(
                zmq.SUBSCRIBE, self.zmq_config.zone_exit_topic
            )
            self.logger.debug(
                "Connected to BraidPublisher and TriggerHandler (zone events)."
            )
        except Exception as e:
            self.logger.error(f"Error connecting to ZMQ sockets: {e}")
            raise

    def _receive_message(
        self, socket: zmq.Socket, message_type: Literal["braid", "trigger"]
    ) -> Tuple[Optional[str], Optional[Dict]]:
        """
        Receive a message from the specified ZMQ socket.

        Args:
            socket: The ZMQ socket to receive from
            message_type: Type of message ('braid' or 'trigger') for error logging

        Returns:
            Tuple of (topic, parsed_message) or (None, None) if no message available
        """
        try:
            topic, message = socket.recv_multipart(flags=zmq.NOBLOCK)
            topic = topic.decode("utf-8")
            json_data = message.decode("utf-8")
            return topic, json.loads(json_data)
        except zmq.Again:
            return None, None
        except Exception as e:
            self.logger.error(f"Error receiving {message_type} message: {e}")
            return None, None

    def _parse_message(
        self, message: Dict, message_type: Literal["braid", "trigger"]
    ) -> Dict:
        """
        Parse different message types (BRAID tracking or trigger) into a standardized format.

        Args:
            message: The raw message dictionary
            message_type: Type of message ('braid' or 'trigger')

        Returns:
            Standardized message dictionary or None if parsing fails
        """
        if message is None:
            return None

        try:
            if message_type == "trigger":
                # Trigger messages have a simple format with obj_id and frame
                # Just return as is, since it's already in the expected format
                return message

            elif message_type == "braid":
                # BRAID messages have a more complex structure with event types
                if "Birth" in message:
                    # Birth events can be ignored in this implementation
                    return None

                elif "Death" in message:
                    # Death events contain just the obj_id (int)
                    obj_id = message["Death"]
                    return {
                        "event": "Death",
                        "obj_id": obj_id,
                        "frame": None,
                    }

                elif "Update" in message:
                    # Update events contain position and velocity data
                    obj_data = message["Update"]
                    return {
                        "event": "Update",
                        "obj_id": obj_data.get("obj_id"),
                        "frame": obj_data.get("frame"),
                        "timestamp": obj_data.get("timestamp"),
                        "x": obj_data.get("x"),
                        "y": obj_data.get("y"),
                        "z": obj_data.get("z"),
                        "xvel": obj_data.get("xvel"),
                        "yvel": obj_data.get("yvel"),
                        "zvel": obj_data.get("zvel"),
                    }
                elif "CalibrationFlydraXml" in message:
                    return None
                else:
                    self.logger.warning(f"Unknown BRAID message type: {message}")
                    return None
            else:
                self.logger.error(f"Unknown message type: {message_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error parsing {message_type} message: {e}")
            return None

    def _get_position(self, message: Dict) -> Optional[Tuple[float, float, float]]:
        """
        Extract position data for a specific object from the message.

        Args:
            message: The parsed message containing object data

        Returns:
            Tuple of (x, y, z) coordinates or None if position data not available
        """
        try:
            if not message:
                return None

            # For standardized BRAID update messages
            if message.get("event") == "Update":
                x = message.get("x")
                y = message.get("y")
                z = message.get("z")

                # Ensure all position values are present
                if x is not None and y is not None and z is not None:
                    return (x, y, z)

            return None
        except Exception as e:
            self.logger.error(f"Error extracting position data: {e}")
            return None

    def _get_velocity(self, message: Dict) -> Optional[Tuple[float, float, float]]:
        """
        Extract velocity data for a specific object from the message.

        Args:
            message: The parsed message containing object data

        Returns:
            Tuple of (vx, vy, vz) velocities or None if velocity data not available
        """
        try:
            if not message:
                return None

            # For standardized BRAID update messages
            if message.get("event") == "Update":
                vx = message.get("xvel")
                vy = message.get("yvel")
                vz = message.get("zvel")

                # Ensure all velocity values are present
                if vx is not None and vy is not None and vz is not None:
                    return (vx, vy, vz)

            return None
        except Exception as e:
            self.logger.error(f"Error extracting velocity data: {e}")
            return None

    def _update_kalman_filter(self, obj_id: str, message: Dict) -> None:
        """
        Update or initialize the Kalman filter for an object.

        Args:
            obj_id: The ID of the tracked object
            message: The parsed message containing object data
        """
        if not self.lens_config.kalman_enabled:
            return

        try:
            position = self._get_position(message)
            velocity = self._get_velocity(message)
            timestamp = message.get("timestamp")

            if position is None:
                return

            # If we don't have a filter for this object yet, create one
            if obj_id not in self.kalman_filters:
                self.logger.debug(f"Initializing Kalman filter for object {obj_id}")
                self.kalman_filters[obj_id] = KalmanFilter(
                    process_noise=self.lens_config.process_noise,
                    measurement_noise=self.lens_config.measurement_noise,
                    initial_covariance=self.lens_config.initial_covariance,
                )
                # Initialize with the current position, velocity, and timestamp
                self.kalman_filters[obj_id].init(position, velocity, timestamp)
            else:
                # Update the existing filter
                self.kalman_filters[obj_id].update(position, velocity, timestamp)

        except Exception as e:
            self.logger.error(f"Error updating Kalman filter for object {obj_id}: {e}")

    def _clear_kalman_filter(self, obj_id: str) -> None:
        """Remove Kalman filter state for an object when it is no longer tracked."""
        if obj_id in self.kalman_filters:
            self.kalman_filters.pop(obj_id, None)
            self.logger.debug(f"Removed Kalman filter for object {obj_id}")

    def _predict_position(
        self, obj_id: str, prediction_time: float = None
    ) -> Optional[Tuple[float, float, float]]:
        """
        Predict the future position of an object using its Kalman filter.

        Args:
            obj_id: The ID of the tracked object
            prediction_time: Time in the future to predict (seconds)

        Returns:
            Predicted position (x, y, z) or None if prediction fails
        """
        if not self.lens_config.kalman_enabled or obj_id not in self.kalman_filters:
            return None

        try:
            # If no specific prediction time is provided, use the configured prediction horizon
            if prediction_time is None:
                prediction_time = self.lens_config.prediction_horizon

            # Get the predicted position
            return self.kalman_filters[obj_id].predict(prediction_time)

        except Exception as e:
            self.logger.error(f"Error predicting position for object {obj_id}: {e}")
            return None

    def run(self):
        try:
            self.initialize()
        except Exception as e:
            self.logger.error(f"Liquid Lens failed to initialize: {e}")
            return

        self.is_running = True
        self.logger.info("Liquid Lens process started.")

        # Safety timeout: stop tracking if no position updates for this long
        # (uses global zone_timeout from trigger_handler config)
        position_timeout = self.lens_config.zone_timeout

        while self.is_running and not self.stop_event.is_set():
            try:
                # --- Poll trigger socket for ZONE_ENTER / ZONE_EXIT ---
                topic, raw_msg = self._receive_message(self.trigger_socket, "trigger")
                if topic is not None and raw_msg is not None:
                    if (
                        topic == self.zmq_config.zone_enter_topic
                        and not self.is_tracking
                    ):
                        obj_id = raw_msg.get("obj_id")
                        if obj_id is not None:
                            self.logger.info(
                                f"ZONE_ENTER: start tracking object {obj_id}"
                            )
                            self.is_tracking = True
                            self.current_tracked_obj = obj_id
                            self.last_position_time = time.time()
                            self._log_csv("zone_enter", obj_id=obj_id)
                    elif topic == self.zmq_config.zone_exit_topic and self.is_tracking:
                        if raw_msg.get("obj_id") == self.current_tracked_obj:
                            reason = raw_msg.get("reason", "unknown")
                            self.logger.info(
                                f"ZONE_EXIT: stop tracking object {self.current_tracked_obj} "
                                f"reason={reason}"
                            )
                            self._log_csv(
                                "zone_exit",
                                obj_id=self.current_tracked_obj,
                                reason=reason,
                            )
                            self._clear_kalman_filter(self.current_tracked_obj)
                            self.is_tracking = False
                            self.current_tracked_obj = None

                # --- If tracking, process BRAID updates for lens adjustment ---
                if self.is_tracking:
                    _, braid_raw = self._receive_message(self.braid_socket, "braid")
                    braid_data = self._parse_message(braid_raw, "braid")

                    if braid_data is None:
                        # Check position timeout safety
                        if time.time() - self.last_position_time > position_timeout:
                            self.logger.warning(
                                f"No position data for {position_timeout}s, stopping tracking"
                            )
                            self._log_csv(
                                "timeout",
                                obj_id=self.current_tracked_obj,
                                reason="position_timeout",
                            )
                            self._clear_kalman_filter(self.current_tracked_obj)
                            self.is_tracking = False
                            self.current_tracked_obj = None
                        else:
                            time.sleep(0.001)
                        continue

                    # Check for Death messages
                    if (
                        braid_data.get("event") == "Death"
                        and braid_data.get("obj_id") == self.current_tracked_obj
                    ):
                        self.logger.info(
                            f"Tracked object {self.current_tracked_obj} died"
                        )
                        self._log_csv(
                            "death",
                            obj_id=self.current_tracked_obj,
                            reason="object_death",
                        )
                        self._clear_kalman_filter(self.current_tracked_obj)
                        self.is_tracking = False
                        self.current_tracked_obj = None
                        continue

                    # Skip messages for other objects
                    if braid_data.get("obj_id") != self.current_tracked_obj:
                        continue

                    self.last_position_time = time.time()

                    position = self._get_position(braid_data)
                    if position is None:
                        continue

                    x, y, z = position

                    # Update Kalman filter
                    if self.lens_config.kalman_enabled:
                        self._update_kalman_filter(self.current_tracked_obj, braid_data)

                    # Adjust lens focus
                    try:
                        focus_position = None
                        if self.lens_config.kalman_enabled:
                            prediction_time = (
                                self.lens_config.system_latency
                                + self.lens_config.prediction_horizon
                            )
                            predicted_position = self._predict_position(
                                self.current_tracked_obj, prediction_time
                            )
                            if predicted_position is not None:
                                focus_position = predicted_position[2]
                                self.logger.debug(
                                    f"Predicted z={focus_position:.3f} (current z={z:.3f})"
                                )

                        if focus_position is None:
                            focus_position = z

                        dpt = self.lens_calibration.get_dpt(focus_position)
                        self.lens_driver.set_diopter(dpt)
                        self.logger.debug(
                            f"Setting lens to {dpt} diopters for z={focus_position}"
                        )
                        self._log_csv(
                            "focus",
                            obj_id=self.current_tracked_obj,
                            x=x,
                            y=y,
                            z=z,
                            focus_z=focus_position,
                            diopter=dpt,
                            kalman=self.lens_config.kalman_enabled,
                        )
                    except Exception as e:
                        self.logger.error(f"Error adjusting lens: {e}")

                else:
                    # Not tracking — short sleep to avoid busy waiting
                    time.sleep(0.01)

            except Exception as e:
                self.logger.error(f"Error in Liquid Lens process: {e}")

        self.logger.info("Liquid Lens process stopped.")
        self.close()
        self.logger.info("Liquid Lens process closed.")

    def close(self):
        """Close all resources and connections."""
        self.is_running = False
        self.is_tracking = False

        # Close CSV writer
        if self.csv_writer:
            try:
                self.csv_writer.close()
                self.logger.debug("CSV writer closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing CSV writer: {e}")

        # Clean up Kalman filters
        if hasattr(self, "kalman_filters") and self.kalman_filters:
            self.kalman_filters.clear()
            self.logger.debug("Kalman filters cleared")

        # Close lens driver if it exists
        if hasattr(self, "lens_driver") and self.lens_driver:
            try:
                self.lens_driver.close()
                self.logger.debug("Lens driver closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing lens driver: {e}")

        # Close ZMQ sockets if they exist
        if hasattr(self, "braid_socket") and self.braid_socket:
            try:
                self.braid_socket.close()
                self.logger.debug("Braid socket closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing Braid socket: {e}")

        if hasattr(self, "trigger_socket") and self.trigger_socket:
            try:
                self.trigger_socket.close()
                self.logger.debug("Trigger socket closed successfully")
            except Exception as e:
                self.logger.error(f"Error closing Trigger socket: {e}")

        # Terminate ZMQ context if it exists
        if hasattr(self, "context") and self.context:
            try:
                self.context.term()
                self.logger.debug("ZMQ context terminated successfully")
            except Exception as e:
                self.logger.error(f"Error terminating ZMQ context: {e}")

        self.logger.info("Liquid Lens process closed.")
