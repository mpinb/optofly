"""
Trigger Handler module that processes tracking data and generates zone events.

This module subscribes to the Braid server's ZMQ feed, processes object tracking data,
and emits ZONE_ENTER / ZONE_EXIT events when objects enter or leave the trigger zone
(camera FOV x/y + z bounds) while heading toward center.
"""

from collections import deque
from dataclasses import dataclass, field
import json
import math
import multiprocessing as mp
import time
from typing import Any, Dict, Optional, Tuple
import numpy as np
import zmq

from src.utils.config import TriggerHandlerConfig
from src.utils.logger import configure_process_logging
from src.utils.worker import WorkerProcess

# Constants
HEADING_HISTORY_SIZE = 10  # Number of frames to keep for heading calculation
DEFAULT_HEADING_THRESHOLD = np.deg2rad(
    45.0
)  # Fallback cone half-angle if config fails to load
MAX_OBJECT_AGE = 10.0  # Maximum time in seconds to keep an object without updates


def _should_run_cleanup(
    tracked_objects: dict, elapsed_since_last: float, interval: float = 5.0
) -> bool:
    """Decide whether _cleanup_stale_objects() should run this iteration.

    Runs every iteration while any object is in_zone (a timed-out in-zone
    object should trigger ZONE_EXIT close to zone_timeout, not up to
    `interval` seconds later on top of it); otherwise throttled to
    `interval` seconds, matching the original cadence for the common
    no-active-trial case.
    """
    if any(obj.in_zone for obj in tracked_objects.values()):
        return True
    return elapsed_since_last > interval


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

    # Zone membership tracking
    in_zone: bool = False  # ZONE_ENTER has been emitted for this object
    zone_enter_time: Optional[float] = None  # when ZONE_ENTER was emitted
    zone_enter_frame: Optional[int] = None  # Braid frame at which ZONE_ENTER fired
    opto_fired: bool = False  # OPTO_ZONE_ENTER emitted for this zone occupancy
    visual_fired: bool = False  # VISUAL_ZONE_ENTER emitted for this zone occupancy

    # Braid's own detection timestamp for the most recent Update/Birth, kept
    # separate from the receipt-time clock used everywhere else on this
    # object (current_timestamp, last_check_time, first_timestamp) -- those
    # must keep using TriggerHandler's own clock or age/cooldown/velocity
    # math breaks if Braid's clock isn't synced to the local machine.
    current_braid_timestamp: Optional[float] = None

    # Track last time this object was checked
    last_check_time: float = field(default_factory=time.time)

    # Running sums for incremental circular mean (headings) and mean velocity
    _sin_sum: float = 0.0
    _cos_sum: float = 0.0
    _vel_sum: list = field(default_factory=lambda: [0.0, 0.0, 0.0])

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
        self.timestamps.append(timestamp)

        # Incremental mean velocity — evict oldest value before deque auto-drops it
        if len(self.velocities) == self.velocities.maxlen:
            old = self.velocities[0]
            self._vel_sum[0] -= old[0]
            self._vel_sum[1] -= old[1]
            self._vel_sum[2] -= old[2]
        self._vel_sum[0] += xvel
        self._vel_sum[1] += yvel
        self._vel_sum[2] += zvel
        self.velocities.append((xvel, yvel, zvel))

        # Calculate velocity magnitude in xy plane
        velocity_magnitude = math.sqrt(xvel**2 + yvel**2)

        # Only calculate and store heading if object is actually moving
        # This prevents noise from stationary objects triggering false positives
        if velocity_magnitude >= min_velocity:
            heading = math.atan2(yvel, xvel)
            if len(self.headings) == self.headings.maxlen:
                old = self.headings[0]
                self._sin_sum -= math.sin(old)
                self._cos_sum -= math.cos(old)
            self._sin_sum += math.sin(heading)
            self._cos_sum += math.cos(heading)
            self.headings.append(heading)

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
        return math.atan2(self._sin_sum, self._cos_sum)

    def is_heading_toward_center(
        self,
        threshold: float = DEFAULT_HEADING_THRESHOLD,
        center_x: float = 0.0,
        center_y: float = 0.0,
    ) -> bool:
        """
        Determine if the object is moving toward the configured center point.

        Returns:
            True if heading is within threshold of direction to center
        """
        mean_heading = self.get_mean_heading()
        if mean_heading is None:
            return False

        # Calculate angle from current position to target center.
        angle_to_center = np.arctan2(
            center_y - self.current_y, center_x - self.current_x
        )

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
        n = len(self.velocities)
        if n == 0:
            return None
        return (self._vel_sum[0] / n, self._vel_sum[1] / n, self._vel_sum[2] / n)


class TriggerHandler(WorkerProcess):
    """
    Process that evaluates tracking data and emits zone enter/exit events.

    Subscribes to the Braid ZMQ feed, tracks objects, and emits ZONE_ENTER when
    a fly enters the trigger zone (camera FOV + z bounds) while heading toward
    center. Emits ZONE_EXIT when the fly leaves the zone, dies, or times out.
    """

    def __init__(
        self,
        config_path: str = "configs/config.toml",
        event: Optional[mp.Event] = None,
        process_name: str = "TriggerHandler",
        log_level: str = "INFO",
        log_color: str = "MAGENTA",
        log_path: str | None = None,
    ):
        super().__init__(
            event=event,
            log_path=log_path,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        self.config = TriggerHandlerConfig.from_path(config_path)
        self.stop_event = event if event is not None else mp.Event()
        self.is_initialized = False

        # Trigger zone = camera FOV (x/y) + z bounds from trigger_handler
        self.fov_x_min = self.config.fov_x_min
        self.fov_x_max = self.config.fov_x_max
        self.fov_y_min = self.config.fov_y_min
        self.fov_y_max = self.config.fov_y_max
        self.z_min = self.config.z_min
        self.z_max = self.config.z_max
        self.fov_center_x = (self.fov_x_min + self.fov_x_max) / 2.0
        self.fov_center_y = (self.fov_y_min + self.fov_y_max) / 2.0
        self.fov_frustum: bool = self.config.fov_frustum
        if self.fov_frustum:
            self._near_z = self.config.fov_near_z
            self._near_x_min = self.config.fov_near_x_min
            self._near_x_max = self.config.fov_near_x_max
            self._near_y_min = self.config.fov_near_y_min
            self._near_y_max = self.config.fov_near_y_max
            self._far_z = self.config.fov_far_z
            self._far_x_min = self.config.fov_far_x_min
            self._far_x_max = self.config.fov_far_x_max
            self._far_y_min = self.config.fov_far_y_min
            self._far_y_max = self.config.fov_far_y_max

        # Global cooldown period — suppress ZONE_ENTER for this many seconds
        # after the last one was sent, regardless of object identity.
        self.cooldown_period: float = self.config.cooldown_period
        self.opto_zone_scale: float = self.config.opto_zone_scale
        self.visual_zone_scale: float = self.config.visual_zone_scale
        self._last_zone_enter_time: float = 0.0

        # Dictionary to track objects: {obj_id: TrackedObject}
        self.tracked_objects = {}

        # Count of ZONE_ENTER events emitted this run
        self._zone_enter_count = 0

        # ZMQ connections
        self.context = None
        self.subscriber = None
        self.publisher = None

    def initialize(self) -> bool:
        """Initialize the trigger handler and ZMQ connections."""
        self.logger.info("Initializing TriggerHandler")

        try:
            self._initialize_zmq()
            self.is_initialized = True
            self.logger.info(
                f"TriggerHandler initialized successfully "
                f"(cooldown_period={self.cooldown_period}s)"
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize TriggerHandler: {e}")
            return False

    def _initialize_zmq(self) -> None:
        """Initialize ZMQ publisher and subscriber connections."""
        try:
            self.context = zmq.Context()

            # Publisher for zone events (ZONE_ENTER, ZONE_EXIT)
            self.publisher = self.context.socket(zmq.PUB)
            publisher_address = self.config.zmq.get_publisher_address(
                self.config.zmq.trigger_port
            )
            self.logger.info(f"Binding ZMQ publisher to {publisher_address}")
            self.publisher.bind(publisher_address)

            # Subscriber for Braid tracking data
            self.subscriber = self.context.socket(zmq.SUB)
            subscriber_address = self.config.zmq.get_subscriber_address(
                self.config.zmq.braid_port
            )
            self.logger.info(f"Connecting ZMQ subscriber to {subscriber_address}")
            self.subscriber.connect(subscriber_address)

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

    def _get_fov_at_z(self, z: float) -> tuple:
        """Return (x_min, x_max, y_min, y_max) for the given z, interpolating in frustum mode."""
        if not self.fov_frustum:
            return self.fov_x_min, self.fov_x_max, self.fov_y_min, self.fov_y_max
        alpha = (z - self._near_z) / (self._far_z - self._near_z)
        return (
            self._near_x_min + alpha * (self._far_x_min - self._near_x_min),
            self._near_x_max + alpha * (self._far_x_max - self._near_x_max),
            self._near_y_min + alpha * (self._far_y_min - self._near_y_min),
            self._near_y_max + alpha * (self._far_y_max - self._near_y_max),
        )

    def _get_zone_at_z(self, z: float, scale: float) -> tuple:
        """Return (x_min, x_max, y_min, y_max) for the outer FOV at the
        given z, shrunk toward its own center by `scale` (1.0 = unchanged,
        equal to the outer FOV)."""
        x_min, x_max, y_min, y_max = self._get_fov_at_z(z)
        if scale >= 1.0:
            return x_min, x_max, y_min, y_max
        cx = (x_min + x_max) / 2.0
        cy = (y_min + y_max) / 2.0
        half_w = (x_max - x_min) / 2.0 * scale
        half_h = (y_max - y_min) / 2.0 * scale
        return cx - half_w, cx + half_w, cy - half_h, cy + half_h

    def _is_in_scaled_zone(self, x: float, y: float, z: float, scale: float) -> bool:
        """Check if a point is within the FOV shrunk by `scale`, ignoring z bounds."""
        x_min, x_max, y_min, y_max = self._get_zone_at_z(z, scale)
        return x_min <= x <= x_max and y_min <= y <= y_max

    def is_in_trigger_zone(self, x: float, y: float, z: float) -> bool:
        """Check if a point is within the trigger zone (camera FOV x/y + z bounds)."""
        if not (self.z_min <= z <= self.z_max):
            return False
        x_min, x_max, y_min, y_max = self._get_fov_at_z(z)
        return x_min <= x <= x_max and y_min <= y <= y_max

    def is_in_xy_zone(self, x: float, y: float, z: float) -> bool:
        """Check if a point is within the trigger zone x/y bounds only (ignores z)."""
        x_min, x_max, y_min, y_max = self._get_fov_at_z(z)
        return x_min <= x <= x_max and y_min <= y <= y_max

    def process_message(self, message_data: Dict[str, Any]) -> None:
        """Process a message from the Braid server."""
        try:
            if "Birth" in message_data:
                self._process_birth(message_data["Birth"])
            elif "Update" in message_data:
                self._process_update(message_data["Update"])
            elif "Death" in message_data:
                self._process_death(message_data["Death"])
            elif "CalibrationFlydraXml" in message_data:
                pass
            else:
                self.logger.warning(f"Unknown message type: {message_data.keys()}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    def _process_birth(self, data: Dict[str, Any]) -> Optional[TrackedObject]:
        """Process a Birth message for a new tracked object.

        Returns:
            The newly tracked object, or None if the payload was malformed
            (already logged here). Callers must check rather than assuming
            tracked_objects now contains obj_id.
        """
        try:
            obj_id = data["obj_id"]
            frame = data["frame"]
            timestamp = time.time()

            tracked_obj = TrackedObject(obj_id=obj_id, first_timestamp=timestamp)
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

            tracked_obj.current_braid_timestamp = data.get("braid_timestamp")
            self.tracked_objects[obj_id] = tracked_obj
            self.logger.debug(
                f"Started tracking object {obj_id} at position "
                f"({data['x']:.3f}, {data['y']:.3f}, {data['z']:.3f})"
            )
            return tracked_obj
        except KeyError as e:
            self.logger.error(f"Missing field in Birth message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing Birth message: {e}")
        return None

    def _process_update(self, data: Dict[str, Any]) -> None:
        """Process an Update message for an existing tracked object."""
        try:
            obj_id = data["obj_id"]
            frame = data["frame"]
            now = time.time()

            if obj_id not in self.tracked_objects:
                self.logger.debug(
                    f"Received Update for unknown object {obj_id}, creating new tracking entry"
                )
                # _process_birth() logs and swallows its own failures, so a
                # malformed payload leaves nothing in tracked_objects. Bail out
                # here rather than indexing it: that raised a second KeyError,
                # reported as "Missing field in Update message: <obj_id>", which
                # named a field that was present and a message that hadn't
                # failed -- sending anyone debugging a Braid feed to the wrong
                # place entirely.
                tracked_obj = self._process_birth(data)
                if tracked_obj is None:
                    return
            else:
                tracked_obj = self.tracked_objects[obj_id]
                tracked_obj.update(
                    x=data["x"],
                    y=data["y"],
                    z=data["z"],
                    xvel=data["xvel"],
                    yvel=data["yvel"],
                    zvel=data["zvel"],
                    timestamp=now,
                    frame=frame,
                    min_velocity=self.config.min_velocity,
                )

            tracked_obj.current_braid_timestamp = data.get("braid_timestamp")
            self._evaluate_zone_transitions(tracked_obj, now)

        except KeyError as e:
            self.logger.error(f"Missing field in Update message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing Update message: {e}")

    def _process_death(self, data) -> None:
        """Process a Death message for a tracked object."""
        try:
            obj_id = data if isinstance(data, int) else data.get("obj_id", data)
            if obj_id in self.tracked_objects:
                tracked_obj = self.tracked_objects[obj_id]
                if tracked_obj.in_zone:
                    self._send_zone_exit(tracked_obj, reason="death")
                self.logger.debug(f"Stopped tracking object {obj_id}")
                del self.tracked_objects[obj_id]
            else:
                self.logger.warning(f"Received Death for unknown object {obj_id}")
        except Exception as e:
            self.logger.error(f"Error processing Death message: {e}")

    def _evaluate_zone_transitions(self, tracked_obj: TrackedObject, now: float) -> None:
        """
        Evaluate zone enter/exit transitions for a tracked object.

        Entry gates (all must pass to emit ZONE_ENTER):
        1. Object has existed long enough (min_tracking_age) — not transient noise
        2. Refractory period elapsed since last ZONE_ENTER
        3. Object is inside trigger zone (FOV x/y + z bounds)
        4. Velocity is reasonable (between min_velocity and max_velocity)
        5. Heading toward center of volume

        Exit: ZONE_EXIT emitted immediately when object leaves zone.
        """
        x, y, z = tracked_obj.current_x, tracked_obj.current_y, tracked_obj.current_z
        in_zone_now = self.is_in_trigger_zone(x, y, z)
        in_xy_zone_now = self.is_in_xy_zone(x, y, z)

        if not tracked_obj.in_zone and in_zone_now:
            # Object just entered the zone — check all entry gates

            # Gate 1: object must have existed long enough
            age = tracked_obj.get_tracking_duration()
            if age < self.config.min_tracking_age:
                return

            # Gate 2: cooldown period
            elapsed = now - self._last_zone_enter_time
            if elapsed < self.cooldown_period:
                self.logger.debug(
                    f"ZONE_ENTER suppressed for obj={tracked_obj.obj_id} "
                    f"(cooldown: {elapsed:.1f}s / {self.cooldown_period:.1f}s)"
                )
                return

            # Gate 3: already satisfied by in_zone_now check above

            # Gate 4: velocity must be reasonable
            mean_vel = tracked_obj.get_mean_velocity()
            if mean_vel is not None:
                speed = np.sqrt(mean_vel[0] ** 2 + mean_vel[1] ** 2)
                if speed < self.config.min_velocity:
                    return
                if speed > self.config.max_velocity:
                    return

            # Gate 5: heading toward center
            if not tracked_obj.is_heading_toward_center(
                self.config.heading_threshold, self.fov_center_x, self.fov_center_y
            ):
                return

            # All gates passed — emit ZONE_ENTER
            tracked_obj.in_zone = True
            tracked_obj.zone_enter_time = now
            tracked_obj.zone_enter_frame = tracked_obj.current_frame
            tracked_obj.opto_fired = False
            tracked_obj.visual_fired = False
            self._send_zone_enter(tracked_obj, now)

        elif tracked_obj.in_zone and not in_xy_zone_now:
            # Left the zone (x/y only — z drift does not trigger exit)
            self._send_zone_exit(tracked_obj, reason="left_fov", now=now)
            tracked_obj.in_zone = False
            tracked_obj.zone_enter_time = None
            tracked_obj.zone_enter_frame = None
            tracked_obj.opto_fired = False
            tracked_obj.visual_fired = False

        if tracked_obj.in_zone and in_zone_now:
            if not tracked_obj.opto_fired and self._is_in_scaled_zone(
                x, y, z, self.opto_zone_scale
            ):
                self._send_scaled_zone_enter(
                    tracked_obj, now, self.config.zmq.opto_enter_topic
                )
                tracked_obj.opto_fired = True
            if not tracked_obj.visual_fired and self._is_in_scaled_zone(
                x, y, z, self.visual_zone_scale
            ):
                self._send_scaled_zone_enter(
                    tracked_obj, now, self.config.zmq.visual_enter_topic
                )
                tracked_obj.visual_fired = True

    def _build_trigger_payload(self, tracked_obj: TrackedObject, now: float) -> dict:
        """Build the common trigger-event payload shared by ZONE_ENTER and
        the scaled opto/visual inner-zone events."""
        return {
            "obj_id": tracked_obj.obj_id,
            "frame": tracked_obj.current_frame,
            "timestamp": now,
            "braid_timestamp": tracked_obj.current_braid_timestamp,
            "handler_timestamp": now,
            "record_frame": tracked_obj.zone_enter_frame,
            "x": tracked_obj.current_x,
            "y": tracked_obj.current_y,
            "z": tracked_obj.current_z,
            "xvel": tracked_obj.velocities[-1][0] if tracked_obj.velocities else 0.0,
            "yvel": tracked_obj.velocities[-1][1] if tracked_obj.velocities else 0.0,
            "zvel": tracked_obj.velocities[-1][2] if tracked_obj.velocities else 0.0,
            "mean_heading": tracked_obj.get_mean_heading(),
        }

    def _send_zone_enter(self, tracked_obj: TrackedObject, now: float) -> None:
        """Emit a ZONE_ENTER event."""
        try:
            message_data = self._build_trigger_payload(tracked_obj, now)
            message = json.dumps(message_data)
            topic = self.config.zmq.zone_enter_topic.encode("utf-8")
            self.publisher.send_multipart([topic, message.encode("utf-8")])
            self._last_zone_enter_time = now
            self._zone_enter_count += 1
            self.logger.info(
                f"ZONE_ENTER #{self._zone_enter_count} obj={tracked_obj.obj_id} "
                f"pos=({tracked_obj.current_x:.3f}, {tracked_obj.current_y:.3f}, {tracked_obj.current_z:.3f})"
            )
        except Exception as e:
            self.logger.error(f"Error sending ZONE_ENTER: {e}")

    def _send_scaled_zone_enter(self, tracked_obj: TrackedObject, now: float, topic: str) -> None:
        """Emit an opto/visual inner-zone entry event. Same payload shape as
        ZONE_ENTER (built via _build_trigger_payload)."""
        try:
            message_data = self._build_trigger_payload(tracked_obj, now)
            message = json.dumps(message_data)
            self.publisher.send_multipart([topic.encode("utf-8"), message.encode("utf-8")])
            self.logger.info(
                f"{topic} obj={tracked_obj.obj_id} pos=({tracked_obj.current_x:.3f}, "
                f"{tracked_obj.current_y:.3f}, {tracked_obj.current_z:.3f})"
            )
        except Exception as e:
            self.logger.error(f"Error sending {topic}: {e}")

    def _send_zone_exit(self, tracked_obj: TrackedObject, reason: str, now: float | None = None) -> None:
        """Emit a ZONE_EXIT event."""
        try:
            if now is None:
                now = time.time()
            duration = (
                now - tracked_obj.zone_enter_time
                if tracked_obj.zone_enter_time
                else 0.0
            )
            message_data = {
                "obj_id": tracked_obj.obj_id,
                "reason": reason,
                "timestamp": now,
                "duration": duration,
            }

            message = json.dumps(message_data)
            topic = self.config.zmq.zone_exit_topic.encode("utf-8")
            self.publisher.send_multipart([topic, message.encode("utf-8")])
            self.logger.info(
                f"ZONE_EXIT obj={tracked_obj.obj_id} reason={reason} "
                f"duration={duration:.2f}s"
            )
        except Exception as e:
            self.logger.error(f"Error sending ZONE_EXIT: {e}")

    def _cleanup_stale_objects(self) -> None:
        """Remove objects that haven't been updated recently.
        If an in-zone object times out, emit ZONE_EXIT first."""
        current_time = time.time()
        stale_ids = []

        for obj_id, obj in self.tracked_objects.items():
            if current_time - obj.last_check_time > self.config.zone_timeout:
                if obj.in_zone:
                    self._send_zone_exit(obj, reason="timeout", now=current_time)
                    obj.in_zone = False
                    obj.zone_enter_time = None
                    obj.zone_enter_frame = None
                    obj.opto_fired = False
                    obj.visual_fired = False

            if current_time - obj.last_check_time > MAX_OBJECT_AGE:
                stale_ids.append(obj_id)

        for obj_id in stale_ids:
            self.logger.debug(f"Removing stale object {obj_id}")
            del self.tracked_objects[obj_id]

    def _run(self) -> None:
        """Main process loop for the trigger handler."""
        if not self.is_initialized and not self.initialize():
            self.logger.error("Failed to initialize, exiting process")
            return

        self.logger.info("Starting TriggerHandler process")

        poller = zmq.Poller()
        poller.register(self.subscriber, zmq.POLLIN)

        cleanup_timer = time.time()

        try:
            while not self.stop_event.is_set():
                # Poll for messages with timeout (1ms for low-latency trigger response)
                socks = {s for s, _ in poller.poll(1)}

                if self.subscriber in socks:
                    try:
                        topic, message = self.subscriber.recv_multipart()
                        topic = topic.decode("utf-8")
                        json_str = message.decode("utf-8")
                        message_data = json.loads(json_str)
                        self.process_message(message_data)
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Error decoding JSON message: {e}")
                    except Exception as e:
                        self.logger.error(f"Error processing message: {e}")

                # Periodically clean up stale objects
                current_time = time.time()
                if _should_run_cleanup(self.tracked_objects, current_time - cleanup_timer):
                    self._cleanup_stale_objects()
                    cleanup_timer = current_time

        except KeyboardInterrupt:
            pass

        self.logger.info(f"Stopping TriggerHandler ({self._zone_enter_count} trigger(s) this run)")
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

    import logging

    configure_process_logging(
        None,
        "TriggerHandler",
        "MAGENTA",
        level=getattr(logging, args.log_level.upper(), 20),
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting TriggerHandler process")
    stop_event = mp.Event()
    handler = TriggerHandler(config_path=args.config, event=stop_event)

    try:
        if handler.initialize():
            handler.start()
            logger.info("Press Ctrl+C to stop")
            handler.join()
        else:
            logger.error("Failed to initialize handler")
    except KeyboardInterrupt:
        logger.info("Interrupted, stopping handler...")
        stop_event.set()
        handler.join(timeout=3)
