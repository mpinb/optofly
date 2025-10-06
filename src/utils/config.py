#!/usr/bin/env python3
"""
Configuration classes for the OptoFly system.

These utilities load and validate configuration from TOML files.
"""

import tomllib

# Setup custom logger for this module
import logging

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


class LiquidLensConfig(ConfigBase):
    """Configuration for the Liquid Lens hardware control."""

    def __init__(self, config_path: str = "config.toml"):
        """Initialize LiquidLens configuration.

        Args:
            config_path: Path to the TOML configuration file
        """
        super().__init__(config_path, "liquid_lens")
        config = self._load_config()

        # Status flag
        self.active: bool = config.get("active", False)

        # Hardware configuration
        self.port: str = config["port"]
        self.baudrate: int = config["baudrate"]

        # Control mode
        self.mode: str = config.get("mode", "diopter")
        
        # Calibration and tracking settings
        self.calibration_file: str = config.get("calibration_file", "calibrations/liquid_lens.csv")
        self.tracking_timeout: float = config.get("tracking_timeout", 3.0)
        
        # For lens calibration
        self.interp_file: str = config.get("calibration_file", "calibrations/liquid_lens.csv")
        self.n_elements: int = config.get("n_elements", 1000)
        
        # Get camera FOV boundaries from CameraConfig
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
        # How far in the future to predict (in seconds)
        self.prediction_horizon: float = kalman_config.get("prediction_horizon", 0.1)

        # ZMQ configuration
        self.zmq = ZMQConfig(config_path)


class ZMQConfig(ConfigBase):
    """Configuration for ZMQ communication channels across the system."""

    def __init__(self, config_path: str = "config.toml"):
        """Initialize ZMQ configuration."""
        super().__init__(config_path, "zmq")
        config = self._load_config()

        # Ports
        self.braid_port: int = config["braid_port"]
        self.trigger_port: int = config["trigger_port"]

        # Topics
        self.braid_topic: str = config["braid_topic"]
        self.trigger_topic: str = config["trigger_topic"]
        self.lens_topic: str = config["lens_topic"]

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


class TriggerConfig(ConfigBase):
    """Configuration for the trigger handler."""

    def __init__(self, config_path: str = "config.toml"):
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

    def __init__(self, config_path: str = "config.toml"):
        """Initialize the opto trigger configuration."""
        super().__init__(config_path, "opto_trigger")
        config = self._load_config()

        # Activation flag
        self.active: bool = config.get("active", False)

        # Serial connection details
        self.port: str = config["port"]
        self.baudrate: int = int(config.get("baudrate", 115200))

        # Stimulation parameters
        self.duration: int = int(config.get("duration", 0))
        self.intensity: int = int(config.get("intensity", 0))
        self.frequency: int = int(config.get("frequency", 0))

        # Sham probability controls how often a stimulation is skipped
        self.sham_probability: float = float(config.get("sham_probability", 0.0))

    def get_trigger_command(self) -> str:
        """Return the formatted command string expected by the Arduino firmware."""
        return f"<{self.duration},{self.intensity},{self.frequency}>"

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
            f"  Sham Probability: {self.sham_probability}"
        )


class CameraConfig(ConfigBase):
    """Configuration for the camera and visual field."""

    def __init__(self, config_path: str = "config.toml"):
        """Initialize camera configuration."""
        super().__init__(config_path, "camera")
        config = self._load_config()

        # Camera properties
        self.pixel_size: float = config["pixel_size"] / 1000.0  # Convert from μm to mm
        self.sensor_width_px: int = config["sensor_width_px"]
        self.sensor_height_px: int = config["sensor_height_px"]

        # Calculate sensor dimensions in mm
        self.sensor_width_mm: float = self.sensor_width_px * self.pixel_size
        self.sensor_height_mm: float = self.sensor_height_px * self.pixel_size

        # Field of view dimensions at working distance
        self.working_distance: float = config["working_distance"] / 1000.0  # Convert from mm to m
        self.magnification: float = config["magnification"]

        # Calculate field of view dimensions in meters
        fov_width_m = (self.sensor_width_mm / self.magnification) / 1000.0  # Convert from mm to m
        fov_height_m = (self.sensor_height_mm / self.magnification) / 1000.0

        # Calculate field of view boundaries
        self.fov_x_min: float = -fov_width_m / 2.0
        self.fov_x_max: float = fov_width_m / 2.0
        self.fov_y_min: float = -fov_height_m / 2.0
        self.fov_y_max: float = fov_height_m / 2.0

    def __str__(self):
        """Return a string representation of the configuration."""
        fov_width_mm = (self.fov_x_max - self.fov_x_min) * 1000
        fov_height_mm = (self.fov_y_max - self.fov_y_min) * 1000

        return (
            f"Camera Configuration:\n"
            f"  Sensor: {self.sensor_width_px} x {self.sensor_height_px} px "
            f"({self.sensor_width_mm:.2f} x {self.sensor_height_mm:.2f} mm)\n"
            f"  Pixel Size: {self.pixel_size*1000:.2f} μm\n"
            f"  Working Distance: {self.working_distance*1000:.2f} mm\n"
            f"  Magnification: {self.magnification}x\n"
            f"  Field of View: {fov_width_mm:.2f} x {fov_height_mm:.2f} mm"
        )
