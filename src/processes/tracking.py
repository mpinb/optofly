"""
Trigger Handler module that processes tracking data and generates stimulation triggers.

This module subscribes to the Braid server's ZMQ feed, processes object tracking data,
and sends trigger signals for optical stimulation and liquid lens systems based on
configurable spatial and temporal criteria.
"""

from collections import deque
from dataclasses import dataclass, field
import json
import multiprocessing as mp
import time
from typing import Any, Dict, Optional, Tuple
import numpy as np
from scipy import stats
import zmq

from src.utils.config import ConfigBase, TriggerHandlerConfig, LiquidLensConfig
from src.utils.logger import init_class_logger
from src.utils.worker import WorkerProcess

# Constants
HEADING_HISTORY_SIZE = 10  # Number of frames to keep for heading calculation
DEFAULT_HEADING_THRESHOLD = np.deg2rad(
    45.0
)  # Fallback cone half-angle if config fails to load
MAX_OBJECT_AGE = 10.0  # Maximum time in seconds to keep an object without updates


@dataclass
class TrackedObject:
    """Class to maintain tracking data for a single object."""

    obj_id: int
    first_timestamp: float

    # Recent tracking data (limited history using deques for efficiency)
    positions: deque = field(default_factory=lambda: deque(maxlen=HEADING_HISTORY_SIZE))
    velocities: deque = field(
        default_factory=lambda: deque(maxlen=HEADING_HISTORY_SIZE)
    )
    timestamps: deque = field(
        default_factory=lambda: deque(maxlen=HEADING_HISTORY_SIZE)
    )
    headings: deque = field(default_factory=lambda: deque(maxlen=HEADING_HISTORY_SIZE))

    # Most recent position and frame
    current_x: float = 0.0
    current_y: float = 0.0
    current_z: float = 0.0
    current_frame: int = 0
    current_timestamp: float = 0.0

    # Track last time this object was checked
    last_check_time: float = field(default_factory=time.time)

    def update(
        self,
        x: float,
        y: float,
        z: float,
        xvel: float,
        yvel: float,
        zvel: float,
        timestamp: float,
        frame: int,
        min_velocity: float = 0.01,
    ) -> None:
        """
        Update the tracked object with new position and velocity data.

        Args:
            x, y, z: Position coordinates in meters
            xvel, yvel, zvel: Velocity components in meters/second
            timestamp: Message timestamp in seconds
            frame: Frame number from camera
            min_velocity: Minimum velocity (m/s) to consider object moving
        """
        # Update current position and frame
        self.current_x = x
        self.current_y = y
        self.current_z = z
        self.current_frame = frame
        self.current_timestamp = timestamp

        # Add to history
        self.positions.append((x, y, z))
        self.velocities.append((xvel, yvel, zvel))
        self.timestamps.append(timestamp)

        # Calculate velocity magnitude in xy plane
        velocity_magnitude = np.sqrt(xvel**2 + yvel**2)

        # Only calculate and store heading if object is actually moving
        # This prevents noise from stationary objects triggering false positives
        if velocity_magnitude >= min_velocity:
            heading = np.arctan2(yvel, xvel)
            self.headings.append(heading)
        # If velocity is below threshold, don't add to heading history
        # This ensures stationary objects don't accumulate noisy headings

        # Update last check time
        self.last_check_time = time.time()

    def get_tracking_duration(self, current_time: Optional[float] = None) -> float:
        """
        Calculate how long this object has been tracked.

        Args:
            current_time: Current time in seconds (defaults to time.time())

        Returns:
            Duration in seconds since first observation
        """
        if current_time is None:
            current_time = time.time()
        return current_time - self.first_timestamp

    def get_mean_heading(self) -> Optional[float]:
        """
        Calculate the circular mean of recent headings.

        Returns:
            Mean heading in radians or None if no headings are available
        """
        if not self.headings:
            return None

        # Use scipy.stats.circmean for proper circular mean calculation
        return stats.circmean(list(self.headings), high=np.pi, low=-np.pi)

    def is_heading_toward_center(
        self, threshold: float = DEFAULT_HEADING_THRESHOLD
    ) -> bool:
        """
        Determine if the object is moving toward the center (0,0).

        Returns:
            True if heading is within threshold of direction to center
        """
        mean_heading = self.get_mean_heading()
        if mean_heading is None:
            return False

        # Calculate angle from current position to center (0,0)
        angle_to_center = np.arctan2(-self.current_y, -self.current_x)

        # Calculate angular difference (normalized to [-π, π])
        diff = np.abs(mean_heading - angle_to_center)
        if diff > np.pi:
            diff = 2 * np.pi - diff

        # Object is heading toward center if difference is less than threshold
        return diff < threshold

    def get_mean_velocity(self) -> Optional[Tuple[float, float, float]]:
        """
        Calculate the mean velocity from recent velocity history.

        Returns:
            Tuple of (vx, vy, vz) mean velocities or None if no velocity data
        """
        if not self.velocities:
            return None

        # Calculate mean of recent velocities
        velocities_array = np.array(list(self.velocities))
        mean_vel = np.mean(velocities_array, axis=0)
        return tuple(mean_vel)

    def will_enter_trigger_zone(
        self,
        radius: float,
        z_lim: Tuple[float, float],
        prediction_horizon: float = 2.0,
        time_step: float = 0.1,
    ) -> Tuple[bool, Optional[float]]:
        """
        Predict if the object's current trajectory will intersect the trigger zone.

        Uses linear extrapolation of current position and mean velocity to predict
        future positions. Samples the trajectory at regular intervals to check for
        intersection with the cylindrical trigger zone.

        Args:
            radius: Trigger zone radius in meters
            z_lim: Tuple of (z_min, z_max) for vertical limits in meters
            prediction_horizon: How far ahead to predict in seconds
            time_step: Time interval between prediction samples in seconds

        Returns:
            Tuple of (will_intersect, time_to_intersection)
            - will_intersect: True if trajectory predicted to enter zone
            - time_to_intersection: Time in seconds until entry, or None if no intersection
        """
        # Get mean velocity
        mean_vel = self.get_mean_velocity()
        if mean_vel is None:
            return (False, None)

        vx, vy, vz = mean_vel

        # Check if object is moving (avoid false positives for stationary objects)
        velocity_magnitude = np.sqrt(vx**2 + vy**2 + vz**2)
        if velocity_magnitude < 0.001:  # Essentially stationary
            return (False, None)

        # Current position
        x0, y0, z0 = self.current_x, self.current_y, self.current_z

        # Sample trajectory at regular intervals
        num_steps = int(prediction_horizon / time_step)
        for i in range(1, num_steps + 1):
            t = i * time_step

            # Project position forward
            x_future = x0 + vx * t
            y_future = y0 + vy * t
            z_future = z0 + vz * t

            # Check if in trigger zone (cylinder)
            distance_from_center = np.sqrt(x_future**2 + y_future**2)

            if distance_from_center <= radius and z_lim[0] <= z_future <= z_lim[1]:
                # Trajectory intersects trigger zone!
                return (True, t)

        # No intersection found within prediction horizon
        return (False, None)


class TriggerHandler(WorkerProcess):
    """
    Process that evaluates tracking data and generates stimulation triggers.

    This class subscribes to the Braid server ZMQ feed, processes object tracking data,
    and sends trigger signals for optical stimulation and liquid lens control based
    on configurable spatial and temporal criteria.
    """

    def __init__(
        self,
        config_path: str = "configs/config.toml",
        event: Optional[mp.Event] = None,
        process_name: str = "TriggerHandler",
        log_level: str = "INFO",
        log_color: str = "MAGENTA",
    ):
        """
        Initialize the TriggerHandler.

        Args:
            config_path: Path to the configuration file
            event: Event to signal process termination (created if None)
            process_name: Name to display in logs
            log_level: Logging level to use
            log_color: Color for log messages
        """
        # Pass parameters to parent class (WorkerProcess)
        super().__init__(
            event=event,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        # Initialize TriggerHandler-specific attributes
        self.config_base = ConfigBase(config_path)._load_config()
        self.config = TriggerHandlerConfig(config_path)
        self.lens_config = LiquidLensConfig(config_path)
        self.stop_event = event if event is not None else mp.Event()
        self.is_initialized = False

        # Camera FOV from config
        camera_config = self.config_base.get("camera", {})
        fov_config = camera_config.get("FOV", {})
        self.fov_x_min = fov_config.get("x_min", -0.5)
        self.fov_x_max = fov_config.get("x_max", 0.5)
        self.fov_y_min = fov_config.get("y_min", -0.5)
        self.fov_y_max = fov_config.get("y_max", 0.5)

        # Track when the last trigger was sent
        self.last_trigger_time = 0.0

        # Dictionary to track objects: {obj_id: TrackedObject}
        self.tracked_objects = {}

        # ZMQ connections
        self.context = None
        self.subscriber = None
        self.publisher = None

    def initialize(self) -> bool:
        """
        Initialize the trigger handler and ZMQ connections.

        Returns:
            True if initialization was successful, False otherwise
        """
        # Initialize logger in child process (after spawn)
        self._initialize_logger()
        self.logger.info("Initializing TriggerHandler")

        try:
            self._initialize_zmq()
            self.is_initialized = True
            self.logger.info("TriggerHandler initialized successfully")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize TriggerHandler: {e}")
            return False

    def _initialize_zmq(self) -> None:
        """
        Initialize ZMQ publisher and subscriber connections.
        """
        try:
            # Create ZMQ context
            self.context = zmq.Context()

            # Set up publisher for triggers
            self.publisher = self.context.socket(zmq.PUB)
            publisher_address = self.config.zmq.get_publisher_address(
                self.config.zmq.trigger_port
            )
            self.logger.info(f"Binding ZMQ publisher to {publisher_address}")
            self.publisher.bind(publisher_address)

            # Set up subscriber to receive from Braid
            self.subscriber = self.context.socket(zmq.SUB)
            subscriber_address = self.config.zmq.get_subscriber_address(
                self.config.zmq.braid_port
            )
            self.logger.info(f"Connecting ZMQ subscriber to {subscriber_address}")
            self.subscriber.connect(subscriber_address)

            # Subscribe to Braid messages
            self.subscriber.setsockopt_string(
                zmq.SUBSCRIBE, self.config.zmq.braid_topic
            )
            self.logger.info(f"Subscribed to topic: {self.config.zmq.braid_topic}")

        except zmq.ZMQError as e:
            self.logger.error(f"ZMQ initialization error: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during ZMQ initialization: {e}")
            raise

    def is_in_camera_fov(self, x: float, y: float) -> bool:
        """
        Check if a point is within the camera's field of view.

        Args:
            x: X-coordinate in meters
            y: Y-coordinate in meters

        Returns:
            True if the point is within the FOV
        """
        return (self.fov_x_min <= x <= self.fov_x_max) and (
            self.fov_y_min <= y <= self.fov_y_max
        )

    def is_in_trigger_zone(self, x: float, y: float, z: float) -> bool:
        """
        Check if a point is within the cylindrical trigger zone.

        Args:
            x: X-coordinate in meters
            y: Y-coordinate in meters
            z: Z-coordinate in meters

        Returns:
            True if the point is within the trigger zone
        """
        # Calculate distance from center in xy plane
        distance = np.sqrt(x * x + y * y)

        # Check if within radius and z-limits
        return (
            distance <= self.config.radius
            and self.config.z_lim[0] <= z <= self.config.z_lim[1]
        )

    def process_message(self, message_data: Dict[str, Any]) -> None:
        """
        Process a message from the Braid server.

        Args:
            message_data: Parsed message data
        """
        try:
            # Check message type (Birth, Update, Death)
            if "Birth" in message_data:
                self._process_birth(message_data["Birth"])
            elif "Update" in message_data:
                self._process_update(message_data["Update"])
            elif "Death" in message_data:
                self._process_death(message_data["Death"])
            elif "CalibrationFlydraXml" in message_data:
                # TODO: Ignore these types of messages
                pass
            else:
                self.logger.warning(f"Unknown message type: {message_data.keys()}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    def _process_birth(self, data: Dict[str, Any]) -> None:
        """
        Process a Birth message for a new tracked object.

        Args:
            data: Birth message data
        """
        try:
            obj_id = data["obj_id"]
            frame = data["frame"]
            timestamp = (
                time.time()
            )  # Use local timestamp (Braid messages don't include timestamp)

            # Create new tracked object
            tracked_obj = TrackedObject(obj_id=obj_id, first_timestamp=timestamp)

            # Update with initial position and velocity
            tracked_obj.update(
                x=data["x"],
                y=data["y"],
                z=data["z"],
                xvel=data["xvel"],
                yvel=data["yvel"],
                zvel=data["zvel"],
                timestamp=timestamp,
                frame=frame,
                min_velocity=self.config.min_velocity,
            )

            # Add to tracked objects
            self.tracked_objects[obj_id] = tracked_obj
            self.logger.debug(
                f"Started tracking object {obj_id} at position "
                f"({data['x']:.3f}, {data['y']:.3f}, {data['z']:.3f})"
            )
        except KeyError as e:
            self.logger.error(f"Missing field in Birth message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing Birth message: {e}")

    def _process_update(self, data: Dict[str, Any]) -> None:
        """
        Process an Update message for an existing tracked object.

        Args:
            data: Update message data
        """
        try:
            obj_id = data["obj_id"]
            frame = data["frame"]
            timestamp = (
                time.time()
            )  # Use local timestamp (Braid messages don't include timestamp)

            # Check if we're already tracking this object
            if obj_id not in self.tracked_objects:
                self.logger.debug(
                    f"Received Update for unknown object {obj_id}, creating new tracking entry"
                )
                self._process_birth(data)  # Treat as Birth if not already tracking
                return

            # Update the tracked object
            tracked_obj = self.tracked_objects[obj_id]
            tracked_obj.update(
                x=data["x"],
                y=data["y"],
                z=data["z"],
                xvel=data["xvel"],
                yvel=data["yvel"],
                zvel=data["zvel"],
                timestamp=timestamp,
                frame=frame,
                min_velocity=self.config.min_velocity,
            )

            # Evaluate triggers based on updated position and trajectory
            self._evaluate_triggers(tracked_obj)

        except KeyError as e:
            self.logger.error(f"Missing field in Update message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing Update message: {e}")

    def _process_death(self, data) -> None:
        """
        Process a Death message for a tracked object.

        Args:
            data: Death message data — either an int (obj_id) or a dict with "obj_id" key
        """
        try:
            obj_id = data if isinstance(data, int) else data.get("obj_id", data)
            if obj_id in self.tracked_objects:
                self.logger.debug(f"Stopped tracking object {obj_id}")
                del self.tracked_objects[obj_id]
            else:
                self.logger.warning(f"Received Death for unknown object {obj_id}")
        except Exception as e:
            self.logger.error(f"Error processing Death message: {e}")

    def _evaluate_triggers(self, tracked_obj: TrackedObject) -> None:
        """
        Evaluate whether to send trigger signals based on the object's trajectory.

        Single-stage trigger system:
        - Trigger fires when fly enters trigger zone + heading toward center
        - All systems (camera, opto, visual) activate together

        Args:
            tracked_obj: The tracked object to evaluate
        """
        current_time = time.time()
        x, y, z = tracked_obj.current_x, tracked_obj.current_y, tracked_obj.current_z

        # Check if object is heading toward center
        if not tracked_obj.is_heading_toward_center(self.config.heading_threshold):
            return

        # Check if object has been tracked long enough
        tracking_duration = tracked_obj.get_tracking_duration(current_time)
        if tracking_duration < self.config.min_trajectory_time:
            return

        # PREDICTIVE LENS CHECK (independent of main trigger)
        # Only run if lens is active and predictive tracking is enabled
        if (
            self.lens_config.active
            and self.lens_config.predictive_tracking
            and self.is_in_camera_fov(x, y)
        ):
            # Predict if fly will enter trigger zone
            will_trigger, time_to_trigger = tracked_obj.will_enter_trigger_zone(
                self.config.radius,
                self.config.z_lim,
                self.lens_config.prediction_horizon_trajectory,
                self.lens_config.prediction_time_step,
            )

            if will_trigger:
                # Send LENS trigger to start tracking early
                self._send_lens_trigger(tracked_obj, time_to_trigger)

        # Check global cooldown
        if current_time - self.last_trigger_time < self.config.min_trigger_interval:
            return

        # Check if object is in trigger zone
        if self.is_in_trigger_zone(x, y, z):
            # Send trigger (all systems activate)
            self._send_trigger(tracked_obj)
            # Update last trigger time (enforces global cooldown)
            self.last_trigger_time = current_time

    def _send_trigger(self, tracked_obj: TrackedObject) -> None:
        """
        Send a trigger message for camera recording and optogenetic stimulation.

        Args:
            tracked_obj: The TrackedObject that triggered the action
        """
        try:
            # Get mean heading (may be None if not enough data)
            mean_heading = tracked_obj.get_mean_heading()

            # Create message with all relevant fields
            message_data = {
                "obj_id": tracked_obj.obj_id,
                "frame": tracked_obj.current_frame,
                "braid_timestamp": tracked_obj.current_timestamp,
                "trigger_timestamp": time.time(),
                "mean_heading": mean_heading,
                # Keep old 'timestamp' field for backward compatibility
                "timestamp": tracked_obj.current_timestamp,
            }

            message = json.dumps(message_data)
            self.publisher.send_multipart(
                [self.config.zmq.trigger_topic.encode("utf-8"), message.encode("utf-8")]
            )
            self.logger.info(
                f"Sent TRIGGER for object {tracked_obj.obj_id} "
                f"(frame={tracked_obj.current_frame}, heading={mean_heading})"
            )
        except Exception as e:
            self.logger.error(f"Error sending trigger: {e}")

    def _send_lens_trigger(
        self, tracked_obj: TrackedObject, predicted_entry_time: Optional[float] = None
    ) -> None:
        """
        Send a LENS trigger message for predictive liquid lens tracking.

        Args:
            tracked_obj: The TrackedObject that will likely trigger
            predicted_entry_time: Estimated time until trigger zone entry (seconds)
        """
        try:
            # Get mean heading
            mean_heading = tracked_obj.get_mean_heading()

            # Create message with prediction information
            message_data = {
                "obj_id": tracked_obj.obj_id,
                "frame": tracked_obj.current_frame,
                "braid_timestamp": tracked_obj.current_timestamp,
                "trigger_timestamp": time.time(),
                "mean_heading": mean_heading,
                "predicted_entry_time": predicted_entry_time,
                # Keep old 'timestamp' field for backward compatibility
                "timestamp": tracked_obj.current_timestamp,
            }

            message = json.dumps(message_data)
            self.publisher.send_multipart(
                [self.config.zmq.lens_topic.encode("utf-8"), message.encode("utf-8")]
            )
            self.logger.info(
                f"Sent LENS trigger for object {tracked_obj.obj_id} "
                f"(frame={tracked_obj.current_frame}, predicted_entry={predicted_entry_time:.2f}s)"
            )
        except Exception as e:
            self.logger.error(f"Error sending lens trigger: {e}")

    def _cleanup_stale_objects(self) -> None:
        """Remove objects that haven't been updated recently."""
        current_time = time.time()
        stale_ids = []

        for obj_id, obj in self.tracked_objects.items():
            if current_time - obj.last_check_time > MAX_OBJECT_AGE:
                stale_ids.append(obj_id)

        for obj_id in stale_ids:
            self.logger.debug(f"Removing stale object {obj_id}")
            del self.tracked_objects[obj_id]

    def run(self) -> None:
        """
        Main process loop for the trigger handler.
        """
        if not self.is_initialized and not self.initialize():
            self.logger.error("Failed to initialize, exiting process")
            return

        self.logger.info("Starting TriggerHandler process")

        # Set up poller for non-blocking receive
        poller = zmq.Poller()
        poller.register(self.subscriber, zmq.POLLIN)

        cleanup_timer = time.time()

        try:
            while not self.stop_event.is_set():
                # Poll for messages with timeout (100ms)
                socks = dict(poller.poll(100))

                if self.subscriber in socks and socks[self.subscriber] == zmq.POLLIN:
                    # Process incoming message
                    try:
                        # Receive multipart message (topic, content)
                        topic, message = self.subscriber.recv_multipart()
                        topic = topic.decode("utf-8")
                        json_str = message.decode("utf-8")

                        # Parse JSON message
                        message_data = json.loads(json_str)

                        # Process the message
                        self.process_message(message_data)

                    except json.JSONDecodeError as e:
                        self.logger.error(f"Error decoding JSON message: {e}")
                    except Exception as e:
                        self.logger.error(f"Error processing message: {e}")

                # Periodically clean up stale objects
                current_time = time.time()
                if current_time - cleanup_timer > 5.0:  # Clean up every 5 seconds
                    self._cleanup_stale_objects()
                    cleanup_timer = current_time

        except KeyboardInterrupt:
            pass  # Graceful shutdown via stop_event

        # Clean up
        self.logger.info("Stopping TriggerHandler")
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources."""
        if self.publisher:
            self.publisher.close()

        if self.subscriber:
            self.subscriber.close()

        if self.context:
            self.context.term()

        self.logger.info("TriggerHandler cleaned up successfully")


# Example usage when run directly
if __name__ == "__main__":
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="TriggerHandler Process")
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
    args = parser.parse_args()

    # Configure logging
    logger = init_class_logger(
        instance=None,
        log_level=args.log_level,
        process_name="TriggerHandler",
        init_message="Starting TriggerHandler process",
    )
    # Create and run trigger handler
    stop_event = mp.Event()
    handler = TriggerHandler(config_path=args.config, event=stop_event)

    try:
        if handler.initialize():
            handler.start()
            logger.info("Press Ctrl+C to stop")

            # Wait for process to complete
            handler.join()
        else:
            logger.error("Failed to initialize handler")
    except KeyboardInterrupt:
        logger.info("Interrupted, stopping handler...")
        stop_event.set()
        handler.join(timeout=3)
