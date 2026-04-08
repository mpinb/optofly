#!/usr/bin/env python3
"""
Configuration classes for the OptoFly system.

These utilities load and validate configuration from TOML files.
"""

import tomllib

# Setup custom logger for this module
import logging
import math
from typing import ClassVar, Iterable

logger = logging.getLogger(__name__)


class ConfigBase:
    """Base class for all configuration objects."""

    def __init__(self, config_path: str, section: str = None):
        """Initialize the configuration.

        Args:
            config_path: Path to the configuration file
            section: Optional section in the config file to load
        """
        self.config_path = config_path
        self.section = section

    def _load_config(self):
        """Load configuration from file."""
        try:
            with open(self.config_path, "rb") as f:
                config = tomllib.load(f)

            if self.section is not None:
                if self.section not in config:
                    raise ValueError(
                        f"Section '{self.section}' not found in {self.config_path}"
                    )
                return config[self.section]
            return config
        except FileNotFoundError:
            logger.error(f"Config file not found: {self.config_path}")
            raise
        except Exception as e:
            logger.error(f"Error opening config file: {e}")
            raise


class TriggerHandlerConfig(ConfigBase):
    """Configuration helper for the trigger handler process."""

    def __init__(self, config_path: str = "configs/config.toml"):
        """Load trigger handler specific configuration."""
        super().__init__(config_path, "trigger_handler")
        config = self._load_config()

        # Zone timeout: emit ZONE_EXIT if no updates received for this long
        self.zone_timeout: float = float(config.get("zone_timeout", 2.0))

        # Global refractory period: suppress ZONE_ENTER for this many seconds
        # after the last one, regardless of object identity.
        self.refractory_period: float = float(config.get("refractory_period", 10.0))


        # Trigger zone x/y = camera FOV (single source of truth)
        camera_config = CameraConfig(config_path)
        self.fov_x_min: float = camera_config.fov_x_min
        self.fov_x_max: float = camera_config.fov_x_max
        self.fov_y_min: float = camera_config.fov_y_min
        self.fov_y_max: float = camera_config.fov_y_max

        # Trigger zone z bounds (from trigger_handler section)
        self.z_min: float = float(config.get("z_min", 0.0))
        self.z_max: float = float(config.get("z_max", 0.5))
        if self.z_min >= self.z_max:
            raise ValueError("trigger_handler.z_min must be less than z_max")

        # Heading cone configuration (degrees -> radians)
        self.heading_cone_deg: float = float(config.get("heading_cone_deg", 45.0))
        self.heading_threshold: float = math.radians(self.heading_cone_deg)

        # Velocity bounds (m/s) — object must be moving but not unrealistically fast
        self.min_velocity: float = float(config.get("min_velocity", 0.01))
        self.max_velocity: float = float(config.get("max_velocity", 2.0))

        # Minimum tracking age: object must exist for this long before it can
        # trigger ZONE_ENTER (filters transient noise detections).
        self.min_tracking_age: float = float(config.get("min_tracking_age", 0.1))

        # Communication settings reused across processes
        self.zmq = ZMQConfig(config_path)


class LiquidLensConfig(ConfigBase):
    """Configuration for the Liquid Lens hardware control."""

    def __init__(self, config_path: str = "configs/config.toml"):
        """Initialize LiquidLens configuration.

        Args:
            config_path: Path to the TOML configuration file
        """
        super().__init__(config_path, "liquid_lens")
        config = self._load_config()

        # Hardware configuration
        self.port: str = config["port"]

        # Control mode
        self.mode: str = config.get("mode", "diopter")

        # Calibration and tracking settings
        self.calibration_file: str = config.get(
            "calibration_file", "calibrations/liquid_lens.csv"
        )
        self.tracking_timeout: float = config.get("tracking_timeout", 3.0)

        self.n_elements: int = config.get("n_elements", 1000)

        # Get camera FOV boundaries from CameraConfig (x/y only — lens uses calibration for z)
        camera_config = CameraConfig(config_path)
        self.fov_x_min = camera_config.fov_x_min
        self.fov_x_max = camera_config.fov_x_max
        self.fov_y_min = camera_config.fov_y_min
        self.fov_y_max = camera_config.fov_y_max

        # Kalman filter settings
        kalman_config = config.get("kalman", {})
        # Enable/disable Kalman filtering
        self.kalman_enabled: bool = kalman_config.get("enabled", True)
        # Process noise covariance (how quickly velocity can change)
        self.process_noise: float = kalman_config.get("process_noise", 0.01)
        # Measurement noise covariance (accuracy of position measurements)
        self.measurement_noise: float = kalman_config.get("measurement_noise", 0.1)
        # Initial state covariance (uncertainty in initial state)
        self.initial_covariance: float = kalman_config.get("initial_covariance", 1.0)
        # System latency in seconds (message processing + lens adjustment time)
        self.system_latency: float = kalman_config.get("system_latency", 0.05)
        # How far in the future to predict (in seconds) for Kalman filter
        self.prediction_horizon: float = kalman_config.get("prediction_horizon", 0.1)

        # ZMQ configuration
        self.zmq = ZMQConfig(config_path)


class ZMQConfig(ConfigBase):
    """Configuration for ZMQ communication channels across the system."""

    def __init__(self, config_path: str = "configs/config.toml"):
        """Initialize ZMQ configuration."""
        super().__init__(config_path, "zmq")
        config = self._load_config()

        # Ports
        self.braid_port: int = config["braid_port"]
        self.trigger_port: int = config["trigger_port"]

        # Topics
        self.braid_topic: str = config["braid_topic"]
        self.zone_enter_topic: str = config.get("zone_enter_topic", "ZONE_ENTER")
        self.zone_exit_topic: str = config.get("zone_exit_topic", "ZONE_EXIT")

        # Validate configuration
        self._validate_config()

    def _validate_config(self):
        """Validate the ZMQ configuration."""
        if self.braid_port == self.trigger_port:
            raise ValueError("Braid and trigger ports must be different")

    def get_subscriber_address(self, port: int):
        """Get the subscriber address for a given port."""
        return f"tcp://localhost:{port}"

    def get_publisher_address(self, port: int):
        """Get the publisher address for a given port."""
        return f"tcp://*:{port}"


class BraidPublisherConfig(ConfigBase):
    """Configuration helper for the Braid publisher process."""

    def __init__(self, config_path: str = "configs/config.toml"):
        """Load configuration for the Braid publisher."""
        super().__init__(config_path, "braid_publisher")
        config = self._load_config()

        host = config.get("host", "127.0.0.1")
        events_port = int(config.get("events_port", 8397))
        callback_port = int(config.get("callback_port", 12345))

        self.url: str = f"http://{host}:{events_port}"
        self.callback_url: str = f"http://{host}:{callback_port}"

        self.timeout: float = float(config.get("timeout", 30))
        if self.timeout <= 0:
            raise ValueError("braid_publisher.timeout must be positive")

        self.reconnect_delay: float = float(config.get("reconnect_delay", 5))
        if self.reconnect_delay <= 0:
            raise ValueError("braid_publisher.reconnect_delay must be positive")

        # Shared ZMQ configuration
        self.zmq = ZMQConfig(config_path)

    def __str__(self) -> str:
        """Return a readable description of the Braid publisher configuration."""
        return (
            "BraidPublisher Configuration:\n"
            f"  URL: {self.url}\n"
            f"  Timeout: {self.timeout}s\n"
            f"  Reconnect Delay: {self.reconnect_delay}s\n"
            f"  ZMQ Braid Port: {self.zmq.braid_port}"
        )


class TriggerConfig(ConfigBase):
    """Configuration for the trigger handler."""

    def __init__(self, config_path: str = "configs/config.toml"):
        """Initialize trigger configuration."""
        super().__init__(config_path, "trigger")
        config = self._load_config()

        # Speed thresholds
        self.min_speed: float = config["min_speed"]
        self.max_speed: float = config["max_speed"]
        self.pre_trigger_frames: int = config["pre_trigger_frames"]
        self.post_trigger_frames: int = config["post_trigger_frames"]

        # Frame rate used to convert between time and frames
        self.frame_rate: float = config["frame_rate"]

        # Calculate time equivalents for convenience
        self.pre_trigger_time: float = self.pre_trigger_frames / self.frame_rate
        self.post_trigger_time: float = self.post_trigger_frames / self.frame_rate

        # Get camera config for FOV boundaries
        camera_config = CameraConfig(config_path)
        self.fov_x_min = camera_config.fov_x_min
        self.fov_x_max = camera_config.fov_x_max
        self.fov_y_min = camera_config.fov_y_min
        self.fov_y_max = camera_config.fov_y_max

    def __str__(self):
        """Return a string representation of the configuration."""
        # Calculate FOV dimensions in mm for display
        fov_width_mm = (self.fov_x_max - self.fov_x_min) * 1000
        fov_height_mm = (self.fov_y_max - self.fov_y_min) * 1000

        return (
            f"Trigger Configuration:\n"
            f"  Speed Threshold: {self.min_speed} to {self.max_speed} m/s\n"
            f"  Pre-trigger Time: {self.pre_trigger_time}s ({self.pre_trigger_frames} frames)\n"
            f"  Post-trigger Time: {self.post_trigger_time}s ({self.post_trigger_frames} frames)\n"
            f"  FOV Dimensions: {fov_width_mm:.1f} x {fov_height_mm:.1f} mm\n"
            f"  FOV Boundaries: [{self.fov_x_min}, {self.fov_x_max}] x [{self.fov_y_min}, {self.fov_y_max}] m"
        )


class OptoTriggerConfig(ConfigBase):
    """Configuration for the Arduino-based optical trigger controller."""

    SUPPORTED_COLORS: ClassVar[tuple[str, ...]] = ("red", "green", "blue", "white")

    def __init__(self, config_path: str = "configs/config.toml"):
        """Initialize the opto trigger configuration."""
        super().__init__(config_path, "opto_trigger")
        config = self._load_config()

        # Activation flag
        self.active: bool = config.get("active", False)

        # Serial connection details
        self.port: str = config["port"]
        self.baudrate: int = int(config.get("baudrate", 115200))

        # Stimulation parameters - store as option lists
        self.duration_options: list = self._parse_parameter(
            config.get("duration", 0), "duration"
        )
        self.intensity_options: list = self._parse_parameter(
            config.get("intensity", 0), "intensity"
        )
        self.frequency_options: list = self._parse_parameter(
            config.get("frequency", 0), "frequency"
        )

        # Currently selected values (set when triggered)
        # Initialize with first option for backward compatibility
        self.duration: int = int(self.duration_options[0])
        self.intensity: int = int(self.intensity_options[0])
        self.frequency: int = int(self.frequency_options[0])
        self.color: str = self._normalize_color(config.get("color", "white"))

        # Sham probability controls how often a stimulation is skipped
        self.sham_probability: float = float(config.get("sham_probability", 0.0))

    def get_trigger_command(self) -> str:
        """Return the formatted command string expected by the Arduino firmware."""
        return f"<{self.duration},{self.intensity},{self.frequency},{self.color}>"

    @classmethod
    def _normalize_color(cls, color: str | None) -> str:
        """Return a validated, lower-case color token."""

        if color is None:
            return "white"

        normalized = color.strip().lower()
        if normalized not in cls.SUPPORTED_COLORS:
            supported = ", ".join(cls.SUPPORTED_COLORS)
            raise ValueError(
                f"Invalid opto_trigger color '{color}'. Supported values: {supported}"
            )
        return normalized

    @classmethod
    def valid_colors(cls) -> Iterable[str]:
        """Expose the supported color identifiers for CLI validation."""

        return cls.SUPPORTED_COLORS

    def _parse_parameter(self, value, param_name: str):
        """Parse parameter that can be either a single value or list of options.

        Args:
            value: Either a single number or a list of numbers
            param_name: Name of parameter (for error messages)

        Returns:
            List of possible values (even if input is single value)
        """
        if isinstance(value, list):
            if len(value) == 0:
                raise ValueError(f"{param_name} cannot be an empty list")
            return value
        else:
            # Single value - return as single-item list
            return [value]

    def set_color(self, color: str) -> None:
        """Update the configured color with validation."""

        self.color = self._normalize_color(color)

    def __str__(self) -> str:
        """Return a readable summary of the opto trigger configuration."""
        return (
            "OptoTrigger Configuration:\n"
            f"  Active: {self.active}\n"
            f"  Port: {self.port}\n"
            f"  Baudrate: {self.baudrate}\n"
            f"  Duration: {self.duration} ms\n"
            f"  Intensity: {self.intensity}/255\n"
            f"  Frequency: {self.frequency} Hz\n"
            f"  Color: {self.color}\n"
            f"  Sham Probability: {self.sham_probability}"
        )


class CameraConfig(ConfigBase):
    """Configuration for the camera and visual field."""

    def __init__(self, config_path: str = "configs/config.toml"):
        """Initialize camera configuration."""
        super().__init__(config_path, "camera")
        config = self._load_config()

        resolution = config.get("resolution")
        if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
            raise ValueError(
                "camera.resolution must contain two numeric values: width and height"
            )

        self.sensor_width_px, self.sensor_height_px = (
            int(resolution[0]),
            int(resolution[1]),
        )
        self.resolution = (self.sensor_width_px, self.sensor_height_px)

        if "fps" not in config:
            raise ValueError("camera.fps is required in the configuration")
        self.fps: float = float(config.get("fps"))

        self.width: int = self.sensor_width_px
        self.height: int = self.sensor_height_px

        self.exposure_time: float = float(config.get("exposure_time", 0.0))

        # Communication settings
        self.zmq_address: str = config.get("zmq_address", "127.0.0.1")
        self.zmq_port: str = config.get("zmq_port", "5556")

        # Storage
        self.save_folder: str = config.get("save_folder", "camera_videos")

        fov_config = config.get("FOV", {})
        self.fov_x_min: float = float(fov_config.get("x_min", -0.1))
        self.fov_x_max: float = float(fov_config.get("x_max", 0.1))
        self.fov_y_min: float = float(fov_config.get("y_min", -0.1))
        self.fov_y_max: float = float(fov_config.get("y_max", 0.1))

        if self.fov_x_min >= self.fov_x_max:
            raise ValueError("camera.FOV.x_min must be less than x_max")
        if self.fov_y_min >= self.fov_y_max:
            raise ValueError("camera.FOV.y_min must be less than y_max")

    def __str__(self):
        """Return a string representation of the configuration."""
        fov_width_mm = (self.fov_x_max - self.fov_x_min) * 1000
        fov_height_mm = (self.fov_y_max - self.fov_y_min) * 1000

        return (
            "Camera Configuration:\n"
            f"  Resolution: {self.sensor_width_px} x {self.sensor_height_px} px\n"
            f"  Frame Rate: {self.fps} fps\n"
            f"  Exposure Time: {self.exposure_time} µs\n"
            f"  Field of View: {fov_width_mm:.2f} x {fov_height_mm:.2f} mm\n"
            f"  FOV Boundaries: [{self.fov_x_min}, {self.fov_x_max}] x [{self.fov_y_min}, {self.fov_y_max}] m"
        )
