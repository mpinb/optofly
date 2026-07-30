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


# AppConfig.load() may be called many times per process -- once per
# process/tool that needs a config, plus every standalone script under
# src/tools/ -- and each call re-parses and re-validates all nine config
# sections. Caching the parsed TOML by (path, mtime) keeps those repeated
# calls cheap without re-reading the file from disk each time, while still
# picking up on-disk edits (mtime changes invalidate the cache).
_TOML_CACHE: dict[str, tuple[float, dict]] = {}


def _required_section(data: dict, name: str, config_path: str) -> dict:
    """Return section `name`, or explain precisely what's missing.

    Only for sections carrying at least one key with no sensible default
    (`zmq`, `camera`, `liquid_lens`, `opto_trigger`). Omitting one of those is
    a mistake rather than a request for defaults -- and because AppConfig.load()
    validates every section regardless of active flags, the omission stops the
    whole run, so the message has to point straight at the fix.

    Sections that are fully defaulted (`monitoring`, `logging`,
    `visual_stimuli`, `trigger_handler`, `braid_publisher`) stay optional.
    """
    if name not in data:
        # No config path in the message: AppConfig.load() prefixes it once, so
        # naming it here too would print it three times by the time main.py
        # has added its own header.
        raise ValueError(
            f"Section [{name}] not found.\n"
            f"  Every section must be present even when its subsystem is "
            f"inactive, because the whole file is validated in one pass.\n"
            f"  Copy the [{name}] block from configs/config.example.toml."
        )
    return data[name]


def _load_toml_cached(config_path: str) -> dict:
    mtime = os.stat(config_path).st_mtime
    cached = _TOML_CACHE.get(config_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    _TOML_CACHE[config_path] = (mtime, data)
    return data


def load_toml(config_path: str) -> dict:
    """Parse a TOML file and return the raw tree (cached by path + mtime).

    For the one caller that legitimately needs untyped TOML: the visual
    stimuli file, which has its own per-stimulus schema rather than a fixed
    set of fields. Everything describing the main config should go through
    AppConfig instead of reading TOML directly.
    """
    return _load_toml_cached(config_path)


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
    opto_zone_scale: float
    visual_zone_scale: float
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

        opto_zone_scale = float(section.get("opto_zone_scale", 0.5))
        visual_zone_scale = float(section.get("visual_zone_scale", 1.0))
        if not (0.0 < opto_zone_scale <= 1.0):
            raise ValueError(
                f"trigger_handler.opto_zone_scale must be in (0.0, 1.0], got {opto_zone_scale}"
            )
        if not (0.0 < visual_zone_scale <= 1.0):
            raise ValueError(
                f"trigger_handler.visual_zone_scale must be in (0.0, 1.0], got {visual_zone_scale}"
            )

        return cls(
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
            opto_zone_scale=opto_zone_scale,
            visual_zone_scale=visual_zone_scale,
            zmq=zmq,
        )

    @classmethod
    def from_path(
        cls, config_path: str = "configs/config.toml"
    ) -> "TriggerHandlerConfig":
        return AppConfig.load(config_path).trigger_handler


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
        # Hardware configuration.
        # Serial port for the Optotune ICC-1C controller (see the udev rule
        # in configs/config.example.toml -- its idVendor/idProduct are TBD
        # until real hardware is on hand to check).
        try:
            port = section["port"]
        except KeyError:
            raise ValueError(
                "Missing required config key: liquid_lens.port\n"
                '  Example: port = "/dev/optotune_icc1c"'
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

        return cls(
            port=port,
            mode=section.get("mode", "diopter"),
            calibration_file=section.get(
                "calibration_file", "calibrations/liquid_lens.csv"
            ),
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
        )

    @classmethod
    def from_path(cls, config_path: str = "configs/config.toml") -> "LiquidLensConfig":
        return AppConfig.load(config_path).liquid_lens


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
    opto_enter_topic: str
    visual_enter_topic: str
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

        return cls(
            braid_port=braid_port,
            trigger_port=trigger_port,
            active_braid_port=active_braid_port,
            latency_port=latency_port,
            braid_topic=braid_topic,
            zone_enter_topic=section.get("zone_enter_topic", "ZONE_ENTER"),
            zone_exit_topic=section.get("zone_exit_topic", "ZONE_EXIT"),
            opto_enter_topic=section.get("opto_enter_topic", "OPTO_ZONE_ENTER"),
            visual_enter_topic=section.get("visual_enter_topic", "VISUAL_ZONE_ENTER"),
            active_braid_topic=section.get("active_braid_topic", "ACTIVE_BRAID"),
            braid_pub_hwm=braid_pub_hwm,
            lens_update_conflate=bool(section.get("lens_update_conflate", True)),
            transport=transport,
        )

    @classmethod
    def from_path(cls, config_path: str = "configs/config.toml") -> "ZMQConfig":
        return AppConfig.load(config_path).zmq

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


@dataclass(frozen=True)
class BraidPublisherConfig:
    """Configuration helper for the Braid publisher process."""

    host: str
    callback_port: int
    experiments_path: str
    url: str
    callback_url: str
    timeout: float
    reconnect_delay: float
    zmq: "ZMQConfig"
    zone_timeout: float

    @classmethod
    def from_section(
        cls, section: dict, zmq: "ZMQConfig", trigger_handler: "TriggerHandlerConfig"
    ) -> "BraidPublisherConfig":
        host = section.get("host", "127.0.0.1")
        events_port = int(section.get("events_port", 8397))
        callback_port = int(section.get("callback_port", 12345))
        experiments_path = section.get("experiments_path", "/mnt/data/experiments/")

        timeout = float(section.get("timeout", 30))
        if timeout <= 0:
            raise ValueError("braid_publisher.timeout must be positive")

        reconnect_delay = float(section.get("reconnect_delay", 5))
        if reconnect_delay <= 0:
            raise ValueError("braid_publisher.reconnect_delay must be positive")

        return cls(
            host=host,
            callback_port=callback_port,
            experiments_path=experiments_path,
            url=f"http://{host}:{events_port}",
            callback_url=f"http://{host}:{callback_port}",
            timeout=timeout,
            reconnect_delay=reconnect_delay,
            zmq=zmq,
            zone_timeout=trigger_handler.zone_timeout,
        )

    @classmethod
    def from_path(
        cls, config_path: str = "configs/config.toml"
    ) -> "BraidPublisherConfig":
        return AppConfig.load(config_path).braid_publisher

    def __str__(self) -> str:
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
                '  Example: port = "/dev/opto_trigger"'
            )

        duration_options = cls._parse_parameter(section.get("duration", 0), "duration")
        intensity_options = cls._parse_parameter(
            section.get("intensity", 0), "intensity"
        )
        frequency_options = cls._parse_parameter(
            section.get("frequency", 0), "frequency"
        )

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

        return cls(
            active=section.get("active", False),
            port=port,
            baudrate=int(section.get("baudrate", 115200)),
            duration_options=list(duration_options),
            intensity_options=list(intensity_options),
            frequency_options=list(frequency_options),
            duration=int(duration_options[0]),
            intensity=int(intensity_options[0]),
            frequency=int(frequency_options[0]),
            color=cls._normalize_color(section.get("color", "white")),
            sham_probability=sham_probability,
        )

    @classmethod
    def from_path(cls, config_path: str = "configs/config.toml") -> "OptoTriggerConfig":
        return AppConfig.load(config_path).opto_trigger

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

        return cls(
            active=section.get("active", False),
            sensor_width_px=sensor_width_px,
            sensor_height_px=sensor_height_px,
            resolution=(sensor_width_px, sensor_height_px),
            fps=fps,
            width=sensor_width_px,
            height=sensor_height_px,
            exposure_time=float(section.get("exposure_time", 0.0)),
            max_recording_time=float(section.get("max_recording_time", 3.0)),
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
        )

    @classmethod
    def from_path(cls, config_path: str = "configs/config.toml") -> "CameraConfig":
        return AppConfig.load(config_path).camera

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


@dataclass(frozen=True)
class MonitoringConfig:
    """Configuration for the web monitoring dashboard."""

    active: bool
    host: str
    port: int

    @classmethod
    def from_section(cls, section: dict) -> "MonitoringConfig":
        return cls(
            active=section.get("active", False),
            host=section.get("host", "0.0.0.0"),
            port=int(section.get("port", 5000)),
        )

    @classmethod
    def from_path(cls, config_path: str = "configs/config.toml") -> "MonitoringConfig":
        return AppConfig.load(config_path).monitoring


@dataclass(frozen=True)
class LoggingConfig:
    """Configuration for the root logger level."""

    level: str

    @classmethod
    def from_section(cls, section: dict) -> "LoggingConfig":
        return cls(level=section.get("level", "INFO").upper())

    @classmethod
    def from_path(cls, config_path: str = "configs/config.toml") -> "LoggingConfig":
        return AppConfig.load(config_path).logging

    def level_int(self) -> int:
        return getattr(logging, self.level, logging.INFO)


@dataclass(frozen=True)
class VisualStimuliConfig:
    """Configuration for whether/how the Panda3D visual stimuli process runs."""

    active: bool
    config_file: str

    @classmethod
    def from_section(cls, section: dict) -> "VisualStimuliConfig":
        return cls(
            active=section.get("active", False),
            config_file=section.get("config_file", "configs/visual_stimuli.toml"),
        )

    @classmethod
    def from_path(
        cls, config_path: str = "configs/config.toml"
    ) -> "VisualStimuliConfig":
        return AppConfig.load(config_path).visual_stimuli


@dataclass(frozen=True)
class AppConfig:
    """Root config object: one TOML parse, the whole dependency tree
    assembled in the correct order, no config class constructs another."""

    camera: "CameraConfig"
    trigger_handler: "TriggerHandlerConfig"
    liquid_lens: "LiquidLensConfig"
    zmq: "ZMQConfig"
    braid_publisher: "BraidPublisherConfig"
    opto_trigger: "OptoTriggerConfig"
    monitoring: "MonitoringConfig"
    logging: "LoggingConfig"
    visual_stimuli: "VisualStimuliConfig"

    @classmethod
    def load(cls, config_path: str = "configs/config.toml") -> "AppConfig":
        data = _load_toml_cached(config_path)

        try:
            zmq = ZMQConfig.from_section(_required_section(data, "zmq", config_path))
            camera = CameraConfig.from_section(
                _required_section(data, "camera", config_path)
            )
            trigger_handler = TriggerHandlerConfig.from_section(
                data.get("trigger_handler", {}), camera=camera, zmq=zmq
            )
            liquid_lens = LiquidLensConfig.from_section(
                _required_section(data, "liquid_lens", config_path),
                trigger_handler=trigger_handler,
                camera=camera,
                zmq=zmq,
            )
            braid_publisher = BraidPublisherConfig.from_section(
                data.get("braid_publisher", {}),
                zmq=zmq,
                trigger_handler=trigger_handler,
            )
            opto_trigger = OptoTriggerConfig.from_section(
                _required_section(data, "opto_trigger", config_path)
            )
            monitoring = MonitoringConfig.from_section(data.get("monitoring", {}))
            logging_cfg = LoggingConfig.from_section(data.get("logging", {}))
            visual_stimuli = VisualStimuliConfig.from_section(
                data.get("visual_stimuli", {})
            )
        except ValueError as e:
            # Re-raise with the file named. Individual from_section() validators
            # know the key but not which of the several configs in play (example,
            # local, per-experiment copies in braid folders) is being loaded.
            if str(e).startswith(config_path):
                raise
            raise ValueError(f"{config_path}: {e}") from e

        return cls(
            camera=camera,
            trigger_handler=trigger_handler,
            liquid_lens=liquid_lens,
            zmq=zmq,
            braid_publisher=braid_publisher,
            opto_trigger=opto_trigger,
            monitoring=monitoring,
            logging=logging_cfg,
            visual_stimuli=visual_stimuli,
        )
