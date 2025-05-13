"""
Trigger Handler module that processes tracking data and generates stimulation triggers.

This module subscribes to the Braid server's ZMQ feed, processes object tracking data,
and sends trigger signals for optical stimulation and liquid lens systems based on
configurable spatial and temporal criteria.
"""

import json
import multiprocessing as mp
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Any
from collections import deque

import numpy as np
import zmq
from loguru import logger
from scipy import stats

from config import TriggerHandlerConfig, ConfigBase

# Configure logger
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
)

# Constants
HEADING_HISTORY_SIZE = 10  # Number of frames to keep for heading calculation
HEADING_THRESHOLD = (
    np.pi / 4
)  # Maximum angle difference to consider "heading toward center"
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

    # Most recent position
    current_x: float = 0.0
    current_y: float = 0.0
    current_z: float = 0.0

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
    ) -> None:
        """
        Update the tracked object with new position and velocity data.

        Args:
            x, y, z: Position coordinates in meters
            xvel, yvel, zvel: Velocity components in meters/second
            timestamp: Message timestamp in seconds
        """
        # Update current position
        self.current_x = x
        self.current_y = y
        self.current_z = z

        # Add to history
        self.positions.append((x, y, z))
        self.velocities.append((xvel, yvel, zvel))
        self.timestamps.append(timestamp)

        # Calculate heading (angle of velocity vector in xy plane)
        # Only calculate if velocity is non-zero to avoid division by zero
        if abs(xvel) > 1e-6 or abs(yvel) > 1e-6:
            heading = np.arctan2(yvel, xvel)
            self.headings.append(heading)
        elif len(self.headings) > 0:
            # If velocity is too small, use previous heading
            self.headings.append(self.headings[-1])
        else:
            # If no previous heading, use a default (facing along x-axis)
            self.headings.append(0.0)

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

    def is_heading_toward_center(self) -> bool:
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
        return diff < HEADING_THRESHOLD


class TriggerHandler(mp.Process):
    """
    Process that evaluates tracking data and generates stimulation triggers.

    This class subscribes to the Braid server ZMQ feed, processes object tracking data,
    and sends trigger signals for optical stimulation and liquid lens control based
    on configurable spatial and temporal criteria.
    """

    def __init__(
        self, config_path: str = "config.toml", event: Optional[mp.Event] = None
    ):
        """
        Initialize the TriggerHandler.

        Args:
            config_path: Path to the configuration file
            event: Event to signal process termination (created if None)
        """
        super().__init__()
        self.config_base = ConfigBase(config_path)._load_config()
        self.config = TriggerHandlerConfig(config_path)
        self.stop_event = event if event is not None else mp.Event()
        self.is_initialized = False

        # Camera FOV from config
        camera_config = self.config_base.get("camera", {})
        self.camera_fov = camera_config.get("FOV", [[-0.5, 0.5], [-0.5, 0.5]])

        # Track when the last trigger was sent
        self.last_trigger_time = 0.0

        # Dictionary to track objects: {obj_id: TrackedObject}
        self.tracked_objects = {}

        # ZMQ connections
        self.context = None
        self.subscriber = None
        self.publisher = None

        logger.info(f"TriggerHandler initialized with config: {self.config}")

    def initialize(self) -> bool:
        """
        Initialize the trigger handler and ZMQ connections.

        Returns:
            True if initialization was successful, False otherwise
        """
        try:
            self._initialize_zmq()
            self.is_initialized = True
            logger.info("TriggerHandler initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize TriggerHandler: {e}")
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
            logger.info(f"Binding ZMQ publisher to {publisher_address}")
            self.publisher.bind(publisher_address)

            # Set up subscriber to receive from Braid
            self.subscriber = self.context.socket(zmq.SUB)
            subscriber_address = self.config.zmq.get_subscriber_address(
                self.config.zmq.braid_port
            )
            logger.info(f"Connecting ZMQ subscriber to {subscriber_address}")
            self.subscriber.connect(subscriber_address)

            # Subscribe to Braid messages
            self.subscriber.setsockopt_string(
                zmq.SUBSCRIBE, self.config.zmq.braid_topic
            )
            logger.info(f"Subscribed to topic: {self.config.zmq.braid_topic}")

        except zmq.ZMQError as e:
            logger.error(f"ZMQ initialization error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during ZMQ initialization: {e}")
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
        try:
            x_min, x_max = self.camera_fov[0]
            y_min, y_max = self.camera_fov[1]
            return (x_min <= x <= x_max) and (y_min <= y <= y_max)
        except (IndexError, TypeError) as e:
            logger.error(f"Error checking camera FOV: {e}, using default FOV check")
            # Default to a square FOV if config is invalid
            return -0.5 <= x <= 0.5 and -0.5 <= y <= 0.5

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
            else:
                logger.warning(f"Unknown message type: {message_data.keys()}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def _process_birth(self, data: Dict[str, Any]) -> None:
        """
        Process a Birth message for a new tracked object.

        Args:
            data: Birth message data
        """
        try:
            obj_id = data["obj_id"]
            timestamp = data["timestamp"]

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
            )

            # Add to tracked objects
            self.tracked_objects[obj_id] = tracked_obj
            logger.debug(
                f"Started tracking object {obj_id} at position "
                f"({data['x']:.3f}, {data['y']:.3f}, {data['z']:.3f})"
            )
        except KeyError as e:
            logger.error(f"Missing field in Birth message: {e}")
        except Exception as e:
            logger.error(f"Error processing Birth message: {e}")

    def _process_update(self, data: Dict[str, Any]) -> None:
        """
        Process an Update message for an existing tracked object.

        Args:
            data: Update message data
        """
        try:
            obj_id = data["obj_id"]
            timestamp = data["timestamp"]

            # Check if we're already tracking this object
            if obj_id not in self.tracked_objects:
                logger.warning(
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
            )

            # Evaluate triggers based on updated position and trajectory
            self._evaluate_triggers(tracked_obj)

        except KeyError as e:
            logger.error(f"Missing field in Update message: {e}")
        except Exception as e:
            logger.error(f"Error processing Update message: {e}")

    def _process_death(self, obj_id: int) -> None:
        """
        Process a Death message for a tracked object.

        Args:
            obj_id: ID of the object to remove from tracking
        """
        try:
            if obj_id in self.tracked_objects:
                logger.debug(f"Stopped tracking object {obj_id}")
                del self.tracked_objects[obj_id]
            else:
                logger.warning(f"Received Death for unknown object {obj_id}")
        except Exception as e:
            logger.error(f"Error processing Death message: {e}")

    def _evaluate_triggers(self, tracked_obj: TrackedObject) -> None:
        """
        Evaluate whether to send trigger signals based on the object's trajectory.

        Args:
            tracked_obj: The tracked object to evaluate
        """
        current_time = time.time()
        x, y, z = tracked_obj.current_x, tracked_obj.current_y, tracked_obj.current_z

        # Check if object is heading toward center
        if not tracked_obj.is_heading_toward_center():
            return

        # Process LENS trigger (if liquid lens is active)
        # This happens independently of other triggers and doesn't update last_trigger_time
        if self.config.liquid_lens_active and self.is_in_camera_fov(x, y):
            self._send_lens_trigger(tracked_obj.obj_id)

        # Check if object has been tracked long enough
        tracking_duration = tracked_obj.get_tracking_duration(current_time)
        if tracking_duration < self.config.min_trajectory_time:
            return

        # Check if enough time has passed since last trigger
        if current_time - self.last_trigger_time < self.config.min_trigger_interval:
            return

        # Check if object is in trigger zone
        if self.is_in_trigger_zone(x, y, z):
            # Send trigger and update last trigger time
            self._send_trigger(tracked_obj.obj_id)
            self.last_trigger_time = current_time

    def _send_trigger(self, obj_id: int) -> None:
        """
        Send a trigger message for optogenetic stimulation.

        Args:
            obj_id: ID of the object that triggered the stimulation
        """
        if not self.config.opto_trigger_active:
            logger.debug(
                f"Stimulation trigger skipped for object {obj_id} (opto_trigger not active)"
            )
            return

        try:
            timestamp = time.time()
            message = json.dumps({"timestamp": timestamp, "obj_id": obj_id})

            self.publisher.send_string(f"{self.config.zmq.trigger_topic} {message}")
            logger.info(f"Sent TRIGGER for object {obj_id} at {timestamp:.3f}")
        except Exception as e:
            logger.error(f"Error sending trigger: {e}")

    def _send_lens_trigger(self, obj_id: int) -> None:
        """
        Send a trigger message for the liquid lens system.

        Args:
            obj_id: ID of the object that triggered the lens system
        """
        try:
            timestamp = time.time()
            message = json.dumps({"timestamp": timestamp, "obj_id": obj_id})

            self.publisher.send_string(f"{self.config.zmq.lens_topic} {message}")
            logger.debug(f"Sent LENS trigger for object {obj_id} at {timestamp:.3f}")
        except Exception as e:
            logger.error(f"Error sending lens trigger: {e}")

    def _cleanup_stale_objects(self) -> None:
        """Remove objects that haven't been updated recently."""
        current_time = time.time()
        stale_ids = []

        for obj_id, obj in self.tracked_objects.items():
            if current_time - obj.last_check_time > MAX_OBJECT_AGE:
                stale_ids.append(obj_id)

        for obj_id in stale_ids:
            logger.debug(f"Removing stale object {obj_id}")
            del self.tracked_objects[obj_id]

    def run(self) -> None:
        """
        Main process loop for the trigger handler.
        """
        if not self.is_initialized and not self.initialize():
            logger.error("Failed to initialize, exiting process")
            return

        logger.info("Starting TriggerHandler process")

        # Set up poller for non-blocking receive
        poller = zmq.Poller()
        poller.register(self.subscriber, zmq.POLLIN)

        cleanup_timer = time.time()

        while not self.stop_event.is_set():
            # Poll for messages with timeout (100ms)
            socks = dict(poller.poll(100))

            if self.subscriber in socks and socks[self.subscriber] == zmq.POLLIN:
                # Process incoming message
                try:
                    # Receive multipart message (topic, content)
                    topic, message = self.subscriber.recv_multipart()
                    topic_str = topic.decode("utf-8")
                    message_str = message.decode("utf-8")

                    # Parse JSON message
                    message_data = json.loads(message_str)

                    # Process the message
                    self.process_message(message_data)

                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding JSON message: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")

            # Periodically clean up stale objects
            current_time = time.time()
            if current_time - cleanup_timer > 5.0:  # Clean up every 5 seconds
                self._cleanup_stale_objects()
                cleanup_timer = current_time

        # Clean up
        logger.info("Stopping TriggerHandler")
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources."""
        if self.publisher:
            self.publisher.close()

        if self.subscriber:
            self.subscriber.close()

        if self.context:
            self.context.term()

        logger.info("TriggerHandler cleaned up successfully")


# Example usage when run directly
if __name__ == "__main__":
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="TriggerHandler Process")
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
    args = parser.parse_args()

    # Configure logging
    logger.remove()
    logger.add(sys.stderr, level=args.log_level)

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
