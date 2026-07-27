#!/usr/bin/env python3
"""
Configuration classes for the OptoFly system.

These utilities load and validate configuration from TOML files.
"""

import tomllib

# Setup custom logger for this module
import logging
import math
import os
from dataclasses import dataclass
from typing import ClassVar, Iterable

logger = logging.getLogger(__name__)


# Every `*Config` class re-parses the same TOML file per process, and
# several classes construct sibling config classes for shared values
# (e.g. LiquidLensConfig -> TriggerHandlerConfig -> CameraConfig), so a
# single process can otherwise open one file hundreds of times just to
# build one config object. Caching by (path, mtime) keeps repeated
# construction cheap while still picking up on-disk edits.
_TOML_CACHE: dict[str, tuple[float, dict]] = {}


def _load_toml_cached(config_path: str) -> dict:
    mtime = os.stat(config_path).st_mtime
    cached = _TOML_CACHE.get(config_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    _TOML_CACHE[config_path] = (mtime, data)
    return data


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
            config = _load_toml_cached(self.config_path)

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


@dataclass(frozen=True)
class TriggerHandlerConfig:
    """Configuration helper for the trigger handler process."""

    zone_timeout: float
    cooldown_period: float
    fov_x_min: float
    fov_x_max: float
    fov_y_min: float
    fov_y_max: float
    fov_frustum: bool
    fov_near_z: float | None
    fov_near_x_min: float | None
    fov_near_x_max: float | None
    fov_near_y_min: float | None
    fov_near_y_max: float | None
    fov_far_z: float | None
    fov_far_x_min: float | None
    fov_far_x_max: float | None
    fov_far_y_min: float | None
    fov_far_y_max: float | None
    z_min: float
    z_max: float
    heading_cone_deg: float
    heading_threshold: float
    min_velocity: float
    max_velocity: float
    min_tracking_age: float
    zmq: "ZMQConfig"

    @classmethod
    def from_section(
        cls, section: dict, camera: "CameraConfig", zmq: "ZMQConfig"
    ) -> "TriggerHandlerConfig":
        z_min = float(section.get("z_min", 0.0))
        z_max = float(section.get("z_max", 0.5))
        if z_min >= z_max:
            raise ValueError("trigger_handler.z_min must be less than z_max")

        heading_cone_deg = float(section.get("heading_cone_deg", 45.0))

        instance = object.__new__(cls)
        object.__setattr__(instance, "__dict__", dict(
            zone_timeout=float(section.get("zone_timeout", 2.0)),
            cooldown_period=float(section.get("cooldown_period", 10.0)),
            fov_x_min=camera.fov_x_min,
            fov_x_max=camera.fov_x_max,
            fov_y_min=camera.fov_y_min,
            fov_y_max=camera.fov_y_max,
            fov_frustum=camera.fov_frustum,
            fov_near_z=camera.fov_near_z,
            fov_near_x_min=camera.fov_near_x_min,
            fov_near_x_max=camera.fov_near_x_max,
            fov_near_y_min=camera.fov_near_y_min,
            fov_near_y_max=camera.fov_near_y_max,
            fov_far_z=camera.fov_far_z,
            fov_far_x_min=camera.fov_far_x_min,
            fov_far_x_max=camera.fov_far_x_max,
            fov_far_y_min=camera.fov_far_y_min,
            fov_far_y_max=camera.fov_far_y_max,
            z_min=z_min,
            z_max=z_max,
            heading_cone_deg=heading_cone_deg,
            heading_threshold=math.radians(heading_cone_deg),
            min_velocity=float(section.get("min_velocity", 0.01)),
            max_velocity=float(section.get("max_velocity", 2.0)),
            min_tracking_age=float(section.get("min_tracking_age", 0.1)),
            zmq=zmq,
        ))
        return instance

    def __init__(self, config_path: str = "configs/config.toml"):
        """Backward-compatible path-based constructor; reimplemented in
        Task 8 to delegate to AppConfig.load(). For now, builds its own
        CameraConfig/ZMQConfig -- Task 8 removes this last cross-construction
        by routing through AppConfig.load()'s single parse instead."""
        data = _load_toml_cached(config_path)
        section = data.get("trigger_handler", {})
        camera = CameraConfig(config_path)
        zmq = ZMQConfig(config_path)
        built = TriggerHandlerConfig.from_section(section, camera=camera, zmq=zmq)
        object.__setattr__(self, "__dict__", dict(built.__dict__))


@dataclass(frozen=True)
class LiquidLensConfig:
    """Configuration for the Liquid Lens hardware control."""

    port: str
    mode: str
    calibration_file: str
    calibration_model: str
    max_diopter_step: float
    zone_timeout: float
    fov_x_min: float
    fov_x_max: float
    fov_y_min: float
    fov_y_max: float
    predictor: str
    system_latency: float
    prediction_horizon: float
    zmq: "ZMQConfig"

    @classmethod
    def from_section(
        cls,
        section: dict,
        trigger_handler: "TriggerHandlerConfig",
        camera: "CameraConfig",
        zmq: "ZMQConfig",
    ) -> "LiquidLensConfig":
        try:
            port = section["port"]
        except KeyError:
            raise ValueError(
                'Missing required config key: liquid_lens.port\n  Example: port = "/dev/optotune_ld"'
            )

        calibration_model = section.get("calibration_model", "quadratic")
        valid = ("linear", "quadratic", "power", "inverse")
        if calibration_model not in valid:
            raise ValueError(
                f"liquid_lens.calibration_model must be one of {valid}, got {calibration_model!r}"
            )

        predictor = section.get("predictor", "none")
        if predictor not in ("none", "linear"):
            raise ValueError(
                f"liquid_lens.predictor must be 'none' or 'linear', got '{predictor}'"
            )

        kalman_config = section.get("kalman", {})

        instance = object.__new__(cls)
        object.__setattr__(instance, "__dict__", dict(
            port=port,
            mode=section.get("mode", "diopter"),
            calibration_file=section.get("calibration_file", "calibrations/liquid_lens.csv"),
            calibration_model=calibration_model,
            max_diopter_step=float(section.get("max_diopter_step", 0.0)),
            zone_timeout=trigger_handler.zone_timeout,
            fov_x_min=camera.fov_x_min,
            fov_x_max=camera.fov_x_max,
            fov_y_min=camera.fov_y_min,
            fov_y_max=camera.fov_y_max,
            predictor=predictor,
            system_latency=kalman_config.get("system_latency", 0.05),
            prediction_horizon=kalman_config.get("prediction_horizon", 0.05),
            zmq=zmq,
        ))
        return instance

    def __init__(self, config_path: str = "configs/config.toml"):
        """Backward-compatible path-based constructor; reimplemented in
        Task 8 to delegate to AppConfig.load()."""
        data = _load_toml_cached(config_path)
        section = data.get("liquid_lens", {})
        zmq = ZMQConfig(config_path)
        camera = CameraConfig(config_path)
        trigger_handler = TriggerHandlerConfig(config_path)
        built = LiquidLensConfig.from_section(
            section, trigger_handler=trigger_handler, camera=camera, zmq=zmq
        )
        object.__setattr__(self, "__dict__", dict(built.__dict__))


@dataclass(frozen=True)
class ZMQConfig:
    """Configuration for ZMQ communication channels across the system."""

    braid_port: int
    trigger_port: int
    active_braid_port: int
    latency_port: int
    braid_topic: str
    zone_enter_topic: str
    zone_exit_topic: str
    active_braid_topic: str
    braid_pub_hwm: int
    lens_update_conflate: bool
    transport: str

    @classmethod
    def from_section(cls, section: dict) -> "ZMQConfig":
        try:
            braid_port = section["braid_port"]
        except KeyError:
            raise ValueError(
                "Missing required config key: zmq.braid_port\n  Example: braid_port = 5555"
            )
        try:
            trigger_port = section["trigger_port"]
        except KeyError:
            raise ValueError(
                "Missing required config key: zmq.trigger_port\n  Example: trigger_port = 5556"
            )
        active_braid_port = int(section.get("active_braid_port", 5557))
        latency_port = int(section.get("latency_port", 5558))

        try:
            braid_topic = section["braid_topic"]
        except KeyError:
            raise ValueError(
                'Missing required config key: zmq.braid_topic\n  Example: braid_topic = "BRAID"'
            )

        braid_pub_hwm = int(section.get("braid_pub_hwm", 1000))
        transport = section.get("transport", "tcp")
        if transport not in ("tcp", "ipc"):
            raise ValueError(f"zmq.transport must be 'tcp' or 'ipc', got {transport!r}")

        ports = {braid_port, trigger_port, active_braid_port, latency_port}
        if len(ports) != 4:
            raise ValueError(
                "Braid, trigger, active braid, and latency ports must be different"
            )
        if braid_pub_hwm <= 0:
            raise ValueError("zmq.braid_pub_hwm must be positive")

        instance = object.__new__(cls)
        object.__setattr__(instance, "__dict__", dict(
            braid_port=braid_port,
            trigger_port=trigger_port,
            active_braid_port=active_braid_port,
            latency_port=latency_port,
            braid_topic=braid_topic,
            zone_enter_topic=section.get("zone_enter_topic", "ZONE_ENTER"),
            zone_exit_topic=section.get("zone_exit_topic", "ZONE_EXIT"),
            active_braid_topic=section.get("active_braid_topic", "ACTIVE_BRAID"),
            braid_pub_hwm=braid_pub_hwm,
            lens_update_conflate=bool(section.get("lens_update_conflate", True)),
            transport=transport,
        ))
        return instance

    def __init__(self, config_path: str = "configs/config.toml"):
        """Backward-compatible path-based constructor; reimplemented in
        Task 8 to delegate to AppConfig.load()."""
        section = _load_toml_cached(config_path).get("zmq", {})
        built = ZMQConfig.from_section(section)
        object.__setattr__(self, "__dict__", dict(built.__dict__))

    def get_subscriber_address(self, port: int) -> str:
        """Get the subscriber address for a given port."""
        if self.transport == "ipc":
            return f"ipc:///tmp/optofly_{port}.sock"
        return f"tcp://localhost:{port}"

    def get_publisher_address(self, port: int) -> str:
        """Get the publisher address for a given port."""
        if self.transport == "ipc":
            return f"ipc:///tmp/optofly_{port}.sock"
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

        # Zone timeout — used to expire a stuck _active_obj_id if ZONE_EXIT is missed.
        trigger_config = TriggerHandlerConfig(config_path)
        self.zone_timeout: float = trigger_config.zone_timeout

    def __str__(self) -> str:
        """Return a readable description of the Braid publisher configuration."""
        return (
            "BraidPublisher Configuration:\n"
            f"  URL: {self.url}\n"
            f"  Timeout: {self.timeout}s\n"
            f"  Reconnect Delay: {self.reconnect_delay}s\n"
            f"  ZMQ Braid Port: {self.zmq.braid_port}"
        )


@dataclass
class OptoTriggerConfig:
    """Configuration for the Arduino-based optical trigger controller.

    NOT frozen, unlike its siblings: OptoTrigger.set_parameters() mutates
    duration/intensity/frequency/color at runtime, once per trigger, to
    record the balanced-randomization-selected trial parameters. See plan
    Global Constraints for why this is a deliberate exception.
    """

    SUPPORTED_COLORS: ClassVar[tuple[str, ...]] = ("red", "green", "blue", "white")

    active: bool
    port: str
    baudrate: int
    duration_options: list
    intensity_options: list
    frequency_options: list
    duration: int
    intensity: int
    frequency: int
    color: str
    sham_probability: float

    @classmethod
    def from_section(cls, section: dict) -> "OptoTriggerConfig":
        try:
            port = section["port"]
        except KeyError:
            raise ValueError(
                "Missing required config key: opto_trigger.port\n"
                "  Example: port = \"/dev/opto_trigger\""
            )

        duration_options = cls._parse_parameter(section.get("duration", 0), "duration")
        intensity_options = cls._parse_parameter(section.get("intensity", 0), "intensity")
        frequency_options = cls._parse_parameter(section.get("frequency", 0), "frequency")

        sham_probability = float(section.get("sham_probability", 0.0))
        if not (0.0 <= sham_probability <= 1.0):
            raise ValueError(
                f"opto_trigger.sham_probability must be in [0.0, 1.0], "
                f"got {sham_probability}. Use 0.5 for 50% sham trials."
            )

        for v in intensity_options:
            if not (0 <= int(v) <= 255):
                raise ValueError(
                    f"opto_trigger.intensity values must be in [0, 255], got {v}"
                )
        for v in duration_options:
            if int(v) < 0:
                raise ValueError(f"opto_trigger.duration values must be >= 0, got {v}")

        instance = object.__new__(cls)
        instance.__dict__.update(
            active=section.get("active", False),
            port=port,
            baudrate=int(section.get("baudrate", 115200)),
            duration_options=duration_options,
            intensity_options=intensity_options,
            frequency_options=frequency_options,
            duration=int(duration_options[0]),
            intensity=int(intensity_options[0]),
            frequency=int(frequency_options[0]),
            color=cls._normalize_color(section.get("color", "white")),
            sham_probability=sham_probability,
        )
        return instance

    def __init__(self, config_path: str = "configs/config.toml"):
        """Backward-compatible path-based constructor -- reimplemented in
        Task 8 to delegate to AppConfig.load(); for now it parses directly
        so this class is independently usable/testable before AppConfig
        exists.

        Dataclass note: @dataclass only auto-generates __init__ when a class
        doesn't define one itself; since this class defines __init__
        explicitly (for the path-based call form), @dataclass leaves it
        alone and supplies __repr__/__eq__/field annotations only. That
        means from_section() above cannot build instances via cls(...) --
        it would recurse into this path-based __init__, not a field-based
        one -- so it constructs via object.__new__() + direct __dict__
        update instead, exactly like this __init__ does for its own case.
        """
        section = _load_toml_cached(config_path).get("opto_trigger", {})
        built = OptoTriggerConfig.from_section(section)
        self.__dict__.update(built.__dict__)

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

    @staticmethod
    def _parse_parameter(value, param_name: str):
        """Parse parameter that can be either a single value or list of options."""
        if isinstance(value, list):
            if len(value) == 0:
                raise ValueError(f"{param_name} cannot be an empty list")
            return value
        return [value]

    def set_color(self, color: str) -> None:
        """Update the configured color with validation."""
        self.color = self._normalize_color(color)

    def __str__(self) -> str:
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


@dataclass(frozen=True)
class CameraConfig:
    """Configuration for the camera and visual field."""

    active: bool
    sensor_width_px: int
    sensor_height_px: int
    resolution: tuple
    fps: float
    width: int
    height: int
    exposure_time: float
    max_recording_time: float
    zmq_address: str
    zmq_port: str
    save_folder: str
    fov_frustum: bool
    fov_x_min: float
    fov_x_max: float
    fov_y_min: float
    fov_y_max: float
    fov_near_z: float | None
    fov_near_x_min: float | None
    fov_near_x_max: float | None
    fov_near_y_min: float | None
    fov_near_y_max: float | None
    fov_far_z: float | None
    fov_far_x_min: float | None
    fov_far_x_max: float | None
    fov_far_y_min: float | None
    fov_far_y_max: float | None
    braid_ximea_calibration_file: str | None

    @classmethod
    def from_section(cls, section: dict) -> "CameraConfig":
        resolution = section.get("resolution")
        if not isinstance(resolution, (list, tuple)) or len(resolution) != 2:
            raise ValueError(
                "camera.resolution must contain two numeric values: width and height"
            )
        sensor_width_px, sensor_height_px = int(resolution[0]), int(resolution[1])

        if "fps" not in section:
            raise ValueError("camera.fps is required in the configuration")
        fps = float(section.get("fps"))

        fov_config = section.get("FOV", {})
        near = fov_config.get("near")
        far = fov_config.get("far")

        if near and far:
            fov_frustum = True
            fov_near_z = float(near["z"])
            fov_near_x_min = float(near["x_min"])
            fov_near_x_max = float(near["x_max"])
            fov_near_y_min = float(near["y_min"])
            fov_near_y_max = float(near["y_max"])
            fov_far_z = float(far["z"])
            fov_far_x_min = float(far["x_min"])
            fov_far_x_max = float(far["x_max"])
            fov_far_y_min = float(far["y_min"])
            fov_far_y_max = float(far["y_max"])
            if fov_near_z >= fov_far_z:
                raise ValueError("camera.FOV.near.z must be less than camera.FOV.far.z")
            fov_x_min, fov_x_max = fov_far_x_min, fov_far_x_max
            fov_y_min, fov_y_max = fov_far_y_min, fov_far_y_max
        else:
            fov_frustum = False
            fov_near_z = fov_near_x_min = fov_near_x_max = None
            fov_near_y_min = fov_near_y_max = None
            fov_far_z = fov_far_x_min = fov_far_x_max = None
            fov_far_y_min = fov_far_y_max = None
            fov_x_min = float(fov_config.get("x_min", -0.1))
            fov_x_max = float(fov_config.get("x_max", 0.1))
            fov_y_min = float(fov_config.get("y_min", -0.1))
            fov_y_max = float(fov_config.get("y_max", 0.1))
            if fov_x_min >= fov_x_max:
                raise ValueError("camera.FOV.x_min must be less than x_max")
            if fov_y_min >= fov_y_max:
                raise ValueError("camera.FOV.y_min must be less than y_max")

        instance = object.__new__(cls)
        object.__setattr__(instance, "__dict__", dict(
            active=section.get("active", False),
            sensor_width_px=sensor_width_px,
            sensor_height_px=sensor_height_px,
            resolution=(sensor_width_px, sensor_height_px),
            fps=fps,
            width=sensor_width_px,
            height=sensor_height_px,
            exposure_time=float(section.get("exposure_time", 0.0)),
            max_recording_time=float(section.get("max_recording_time", 3.0)),
            zmq_address=section.get("zmq_address", "127.0.0.1"),
            zmq_port=section.get("zmq_port", "5556"),
            save_folder=section.get("save_folder", "camera_videos"),
            fov_frustum=fov_frustum,
            fov_x_min=fov_x_min,
            fov_x_max=fov_x_max,
            fov_y_min=fov_y_min,
            fov_y_max=fov_y_max,
            fov_near_z=fov_near_z,
            fov_near_x_min=fov_near_x_min,
            fov_near_x_max=fov_near_x_max,
            fov_near_y_min=fov_near_y_min,
            fov_near_y_max=fov_near_y_max,
            fov_far_z=fov_far_z,
            fov_far_x_min=fov_far_x_min,
            fov_far_x_max=fov_far_x_max,
            fov_far_y_min=fov_far_y_min,
            fov_far_y_max=fov_far_y_max,
            braid_ximea_calibration_file=section.get("braid_ximea_calibration_file", None),
        ))
        return instance

    def __init__(self, config_path: str = "configs/config.toml"):
        """Backward-compatible path-based constructor; reimplemented in
        Task 8 to delegate to AppConfig.load()."""
        section = _load_toml_cached(config_path).get("camera", {})
        built = CameraConfig.from_section(section)
        object.__setattr__(self, "__dict__", dict(built.__dict__))

    def __str__(self):
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
