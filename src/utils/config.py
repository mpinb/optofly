"""
Configuration classes for OptoFly components.
"""

import logging
import tomllib
from typing import Any, Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class ConfigBase:
    """Base class for configuration objects with common functionality."""

    def __init__(self, config_path: str = "config.toml", section: Optional[str] = None):
        """Initialize configuration from TOML file.

        Args:
            config_path: Path to the TOML configuration file
            section: Section name in the TOML file to read from
        """
        self.config_path = config_path
        self.section = section
        self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from TOML file."""
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

    def _validate_config(self) -> None:
        """Validate port values."""
        for port_name, port_value in [
            ("braid_port", self.braid_port),
            ("trigger_port", self.trigger_port),
        ]:
            if port_value <= 1024 or port_value > 65535:
                raise ValueError(
                    f"{port_name} must be between 1025 and 65535, got: {port_value}"
                )

    def get_publisher_address(self, port: int) -> str:
        """Get the address string for a publisher socket."""
        return f"tcp://*:{port}"

    def get_subscriber_address(self, port: int, host: str = "localhost") -> str:
        """Get the address string for a subscriber socket."""
        return f"tcp://{host}:{port}"

    def __str__(self) -> str:
        """Human-readable representation of configuration."""
        return (
            f"ZMQ Config:\n"
            f"  Braid Port: {self.braid_port}\n"
            f"  Trigger Port: {self.trigger_port}\n"
            f"  Braid Topic: {self.braid_topic}\n"
            f"  Trigger Topic: {self.trigger_topic}\n"
            f"  Lens Topic: {self.lens_topic}"
        )


class TriggerHandlerConfig(ConfigBase):
    """Configuration for the TriggerHandler process.

    This class defines parameters for trajectory-based triggering including
    timing constraints, spatial dimensions, and trigger area geometry.
    All spatial measurements are in meters.
    """

    def __init__(self, config_path: str = "config.toml"):
        """Initialize TriggerHandler configuration.

        Args:
            config_path: Path to the TOML configuration file
        """
        super().__init__(config_path, "trigger_handler")
        config = self._load_config()

        # Timing parameters
        self.min_trajectory_time: float = config["min_trajectory_time"]
        self.min_trigger_interval: float = config["min_trigger_interval"]

        # Spatial parameters (in meters)
        self.radius: float = config["radius"]

        # Z-axis limits
        self.z_lim: List[float] = self._parse_z_lim(config["z_lim"])

        # Component status (from full config)
        full_config = ConfigBase(config_path)._load_config()
        self.opto_trigger_active: bool = full_config.get("opto_trigger", {}).get(
            "active", True
        )
        self.liquid_lens_active: bool = full_config.get("liquid_lens", {}).get(
            "active", False
        )

        # ZMQ configuration
        self.zmq = ZMQConfig(config_path)

        # Validate configuration
        self._validate_config()

    def _parse_z_lim(self, z_lim_value) -> List[float]:
        """Parse Z-limit values with validation."""
        if isinstance(z_lim_value, list) and len(z_lim_value) == 2:
            return [float(z_lim_value[0]), float(z_lim_value[1])]
        else:
            raise ValueError(
                f"z_lim must be a list of two float values, got: {z_lim_value}"
            )

    def _validate_config(self) -> None:
        """Validate configuration values."""
        if self.min_trajectory_time <= 0:
            raise ValueError(
                f"min_trajectory_time must be positive, got: {self.min_trajectory_time}"
            )

        if self.min_trigger_interval <= 0:
            raise ValueError(
                f"min_trigger_interval must be positive, got: {self.min_trigger_interval}"
            )

        if self.radius <= 0:
            raise ValueError(f"radius must be positive, got: {self.radius}")

        if self.z_lim[0] >= self.z_lim[1]:
            raise ValueError(f"z_lim[0] must be less than z_lim[1], got: {self.z_lim}")

    def get_trigger_area_description(self) -> str:
        """Get a human-readable description of the trigger area."""
        radius_cm = self.radius * 100
        z_min_cm = self.z_lim[0] * 100
        z_max_cm = self.z_lim[1] * 100
        height_cm = (self.z_lim[1] - self.z_lim[0]) * 100

        return (
            f"Cylindrical area with radius {radius_cm:.1f} cm, "
            f"height {height_cm:.1f} cm, "
            f"from Z={z_min_cm:.1f} to Z={z_max_cm:.1f} cm"
        )

    def __str__(self) -> str:
        """Human-readable representation of configuration."""
        return (
            f"TriggerHandler Config:\n"
            f"  Min Trajectory Time: {self.min_trajectory_time} seconds\n"
            f"  Min Trigger Interval: {self.min_trigger_interval} seconds\n"
            f"  Trigger Area: {self.get_trigger_area_description()}\n"
            f"  Opto Trigger Active: {self.opto_trigger_active}\n"
            f"  Liquid Lens Active: {self.liquid_lens_active}\n"
            f"  ZMQ: Using shared ZMQ configuration"
        )


class OptoTriggerConfig(ConfigBase):
    """Configuration for optical trigger hardware control."""

    def __init__(self, config_path: str = "config.toml"):
        """Initialize OptoTrigger configuration.

        Args:
            config_path: Path to the TOML configuration file
        """
        super().__init__(config_path, "opto_trigger")
        config = self._load_config()

        # Status flag
        self.active: bool = config.get("active", True)

        # Hardware configuration
        self.port: str = config["port"]
        self.baudrate: int = config["baudrate"]

        # Stimulation parameters
        self.duration: int = config["duration"]
        self.intensity: int = config["intensity"]
        self.frequency: int = config["frequency"]

        # Experimental settings
        self.sham_probability: float = config["sham_probability"]
        self.sham: bool = config.get("sham", False)

        # ZMQ configuration
        self.zmq = ZMQConfig(config_path)

    def get_trigger_command(self) -> str:
        """Generate command string in the format expected by Arduino."""
        return f"<{self.duration},{self.intensity},{self.frequency}>"

    def __str__(self) -> str:
        """Human-readable representation of configuration."""
        return (
            f"OptoTrigger Config:\n"
            f"  Active: {self.active}\n"
            f"  Port: {self.port}\n"
            f"  Baudrate: {self.baudrate}\n"
            f"  Duration: {self.duration}ms\n"
            f"  Intensity: {self.intensity}/255\n"
            f"  Frequency: {self.frequency}Hz\n"
            f"  Sham Probability: {self.sham_probability}\n"
            f"  ZMQ: Using shared ZMQ configuration"
        )


class BraidPublisherConfig(ConfigBase):
    """Configuration for Braid server connection and ZMQ publishing."""

    def __init__(self, config_path: str = "config.toml"):
        """Initialize BraidPublisher configuration.

        Args:
            config_path: Path to the TOML configuration file
        """
        super().__init__(config_path, "braid_publisher")
        config = self._load_config()

        # Server connection
        self.url: str = config["url"]

        # Optional configurations with defaults
        self.reconnect_delay: int = config.get("reconnect_delay", 5)
        self.timeout: int = config.get("timeout", 30)

        # ZMQ configuration
        self.zmq = ZMQConfig(config_path)

    def __str__(self) -> str:
        """Human-readable representation of configuration."""
        return (
            f"Braid Publisher Config:\n"
            f"  URL: {self.url}\n"
            f"  Reconnect Delay: {self.reconnect_delay}s\n"
            f"  Timeout: {self.timeout}s\n"
            f"  ZMQ: Using shared ZMQ configuration"
        )


class CameraConfig(ConfigBase):
    """Configuration for camera settings and field of view."""

    def __init__(self, config_path: str = "config.toml"):
        """Initialize Camera configuration.

        Args:
            config_path: Path to the TOML configuration file
        """
        super().__init__(config_path, "camera")
        config = self._load_config()

        # Camera hardware settings
        self.resolution: Tuple[int, int] = tuple(config.get("resolution", [2016, 2016]))
        self.fps: int = config.get("fps", 500)
        self.exposure_time: int = config.get("exposure_time", 2000)  # in microseconds
        
        # Trigger settings
        self.pre_trigger_time: float = config.get("pre_trigger_time", 0.5)  # in seconds
        self.post_trigger_time: float = config.get("post_trigger_time", 1.5)  # in seconds
        
        # Calculate frames based on timing
        self.pre_trigger_frames: int = int(self.pre_trigger_time * self.fps)
        self.post_trigger_frames: int = int(self.post_trigger_time * self.fps)
        
        # Field of view boundaries (in meters)
        if "FOV" in config:
            fov = config["FOV"]
            self.fov_x_min: float = fov.get("x_min", -0.1)
            self.fov_x_max: float = fov.get("x_max", 0.1)
            self.fov_y_min: float = fov.get("y_min", -0.1)
            self.fov_y_max: float = fov.get("y_max", 0.1)
        else:
            # Default FOV if not specified
            self.fov_x_min: float = -0.1
            self.fov_x_max: float = 0.1
            self.fov_y_min: float = -0.1
            self.fov_y_max: float = 0.1
            
        # Validate configuration
        self._validate_config()

    def _validate_config(self) -> None:
        """Validate configuration values."""
        if len(self.resolution) != 2 or not all(isinstance(dim, int) for dim in self.resolution):
            raise ValueError(f"Resolution must be a tuple of two integers, got: {self.resolution}")
            
        if self.fps <= 0:
            raise ValueError(f"FPS must be positive, got: {self.fps}")
            
        if self.exposure_time <= 0:
            raise ValueError(f"Exposure time must be positive, got: {self.exposure_time}")
            
        if self.pre_trigger_time < 0:
            raise ValueError(f"Pre-trigger time cannot be negative, got: {self.pre_trigger_time}")
            
        if self.post_trigger_time < 0:
            raise ValueError(f"Post-trigger time cannot be negative, got: {self.post_trigger_time}")
            
        if self.fov_x_min >= self.fov_x_max:
            raise ValueError(f"FOV x_min must be less than x_max, got: [{self.fov_x_min}, {self.fov_x_max}]")
            
        if self.fov_y_min >= self.fov_y_max:
            raise ValueError(f"FOV y_min must be less than y_max, got: [{self.fov_y_min}, {self.fov_y_max}]")

    def get_fov_dimensions(self) -> Tuple[float, float]:
        """Get the physical dimensions of the field of view.
        
        Returns:
            Tuple of (width, height) in meters
        """
        width = self.fov_x_max - self.fov_x_min
        height = self.fov_y_max - self.fov_y_min
        return (width, height)
    
    def get_pixel_size(self) -> Tuple[float, float]:
        """Calculate the physical size of each pixel in meters.
        
        Returns:
            Tuple of (width, height) in meters per pixel
        """
        fov_width, fov_height = self.get_fov_dimensions()
        width_per_pixel = fov_width / self.resolution[0]
        height_per_pixel = fov_height / self.resolution[1]
        return (width_per_pixel, height_per_pixel)

    def __str__(self) -> str:
        """Human-readable representation of configuration."""
        # Calculate FOV size in mm for more readable output
        fov_width_mm = (self.fov_x_max - self.fov_x_min) * 1000
        fov_height_mm = (self.fov_y_max - self.fov_y_min) * 1000
        
        return (
            f"Camera Config:\n"
            f"  Resolution: {self.resolution[0]} x {self.resolution[1]}\n"
            f"  FPS: {self.fps}\n"
            f"  Exposure Time: {self.exposure_time} μs\n"
            f"  Pre-trigger Time: {self.pre_trigger_time}s ({self.pre_trigger_frames} frames)\n"
            f"  Post-trigger Time: {self.post_trigger_time}s ({self.post_trigger_frames} frames)\n"
            f"  FOV Dimensions: {fov_width_mm:.1f} x {fov_height_mm:.1f} mm\n"
            f"  FOV Boundaries: [{self.fov_x_min}, {self.fov_x_max}] x [{self.fov_y_min}, {self.fov_y_max}] m"
        )


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

        # ZMQ configuration
        self.zmq = ZMQConfig(config_path)

    def __str__(self) -> str:
        """Human-readable representation of configuration."""
        fov_str = (
            f"  FOV Boundaries: [{self.fov_x_min}, {self.fov_x_max}] x "
            f"[{self.fov_y_min}, {self.fov_y_max}]\n"
        )
            
        return (
            f"Liquid Lens Config:\n"
            f"  Active: {self.active}\n"
            f"  Port: {self.port}\n"
            f"  Baudrate: {self.baudrate}\n"
            f"  Mode: {self.mode}\n"
            f"  Calibration File: {self.calibration_file}\n"
            f"  Tracking Timeout: {self.tracking_timeout}s\n"
            f"{fov_str}"
            f"  ZMQ: Using shared ZMQ configuration"
        )
