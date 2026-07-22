"""Panda3D visual stimuli process for OptoFly.

Subscribes to ZONE_ENTER messages via ZMQ, converts Braid heading to
world degrees, dispatches to registered stimuli, and drives the Panda3D
render loop via the task manager.
"""

import json
import math
import multiprocessing as mp
import time
from pathlib import Path
from typing import Optional

import zmq
from direct.task import Task

from src.utils.worker import WorkerProcess
from src.utils.csv_writer import CSVWriter
from src.utils.trigger_timing import extract_trigger_timing
from src.visual.scene import ArenaScene, DIRECTION_TO_HEADING
from src.visual.stimuli.background import BackgroundStimulus
from src.visual.stimuli.looming import LoomingStimulus
from src.visual.stimuli.oscillating_square import OscillatingSquare


def braid_to_world_heading(braid_rad: float, offset_rad: float, flip: bool) -> float:
    """Convert raw Braid heading to arena world heading in degrees.

    Args:
        braid_rad: Raw heading from Braid tracker (radians)
        offset_rad: Braid value that corresponds to facing the North screen
        flip: True if Braid heading increases in the opposite rotational direction

    Returns:
        World heading in degrees (0=North, 90=East, 180=South, 270=West)
    """
    world_rad = (braid_rad - offset_rad) * (-1.0 if flip else 1.0)
    return math.degrees(world_rad)


class VisualProcess(WorkerProcess):
    """Panda3D visual stimuli process.

    Subscribes to ZONE_ENTER messages via ZMQ, converts Braid heading to
    world degrees, dispatches to registered stimuli, and drives the Panda3D
    render loop at target_fps via the task manager.
    """

    def __init__(
        self,
        config_path: str = "configs/config.toml",
        event: Optional[mp.Event] = None,
        process_name: str = "VisualProcess",
        log_level: str = "INFO",
        log_color: str = "YELLOW",
        standalone: bool = False,
        braid_folder: Optional[str] = None,
        log_path: Optional[str] = None,
    ):
        self.stop_event = event if event is not None else mp.Event()

        super().__init__(
            event=self.stop_event,
            log_path=log_path,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )
        self._config_path = config_path
        self.standalone = standalone
        self.braid_folder = braid_folder

    def _run(self) -> None:
        cfg = self._load_config()
        arena_cfg = cfg.get("arena", {})

        self._offset_rad: float = arena_cfg.get("braid_heading_offset_rad", 0.0)
        self._flip: bool = arena_cfg.get("braid_heading_flip", False)

        # Convert screen_mapping strings to heading degrees
        screen_mapping = arena_cfg.get(
            "screen_mapping", ["North", "East", "South", "West"]
        )
        camera_headings = [
            DIRECTION_TO_HEADING[direction] for direction in screen_mapping
        ]

        window_x_offset: int = arena_cfg.get("window_x_offset", 0)

        self._setup_zmq()
        self._csv_writer = self._setup_csv(cfg)

        if not self.standalone:
            self.logger.info(
                "Creating Panda3D window: %d×1080 at x=%d, %d screens",
                1920 * len(camera_headings),
                window_x_offset,
                len(camera_headings),
            )
            self.logger.info("Screen mapping: %s", " → ".join(screen_mapping))
            self.logger.info(
                "Braid calibration: offset=%.3f rad, flip=%s",
                self._offset_rad,
                self._flip,
            )

        self._scene = ArenaScene(
            viewing_distance_cm=arena_cfg.get("viewing_distance_cm", 25.0),
            camera_headings=camera_headings,
            window_x_offset=window_x_offset,
            standalone=self.standalone,
        )

        if not self.standalone:
            self.logger.info(
                "Actual window size: %d×%d",
                self._scene.win.getXSize(),
                self._scene.win.getYSize(),
            )

        self._stimuli = []
        self._initialize_stimuli(cfg)

        self._scene.taskMgr.add(self._zmq_poll_task, "zmq_poll", sort=0)
        self._scene.taskMgr.add(self._stimulus_update_task, "stimulus_update", sort=1)

        self._scene.run()  # blocks until finalizeExit() is called

        self._close_csv()
        if self._zmq_context:
            self._zmq_context.term()

    def _load_config(self) -> dict:
        from src.utils.config import ConfigBase

        main_cfg = ConfigBase(self._config_path)._load_config()
        vs_path = Path(
            main_cfg.get("visual_stimuli", {}).get(
                "config_file", "configs/visual_stimuli.toml"
            )
        )
        if vs_path.exists():
            raw = ConfigBase(str(vs_path))._load_config()
        else:
            raw = main_cfg
        return raw.get("visual_stimuli", {})

    def _setup_zmq(self) -> None:
        self._zmq_context = None
        self._zmq_socket = None
        self._latency_socket = None
        self._zone_enter_topic = "ZONE_ENTER"
        if self.standalone:
            self.logger.info("Standalone mode — skipping ZMQ setup")
            return
        from src.utils.config import ZMQConfig

        zmq_cfg = ZMQConfig(self._config_path)
        address = zmq_cfg.get_subscriber_address(zmq_cfg.trigger_port)
        self._zone_enter_topic = zmq_cfg.zone_enter_topic
        self.logger.info(
            "Connecting to %s messages at %s",
            zmq_cfg.zone_enter_topic,
            address,
        )
        self._zmq_context = zmq.Context()
        self._zmq_socket = self._zmq_context.socket(zmq.SUB)
        self._zmq_socket.connect(address)
        self._zmq_socket.setsockopt_string(zmq.SUBSCRIBE, zmq_cfg.zone_enter_topic)

        # LATENCY reporting: PUSH connects to LatencyLogger's bound PULL socket.
        self._latency_socket = self._zmq_context.socket(zmq.PUSH)
        self._latency_socket.connect(
            zmq_cfg.get_subscriber_address(zmq_cfg.latency_port)
        )

    def _setup_csv(self, cfg: dict) -> Optional[CSVWriter]:
        log_file = cfg.get("log_file", "stim.csv")
        path = (
            str(Path(self.braid_folder) / log_file) if self.braid_folder else log_file
        )
        return CSVWriter(filepath=path)

    def _close_csv(self) -> None:
        if self._csv_writer:
            self._csv_writer.close()

    def _initialize_stimuli(self, cfg: dict) -> None:
        if cfg.get("background", {}).get("enabled", True):
            stim = BackgroundStimulus(cfg.get("background", {}), self._scene)
            stim.setup()
            self._stimuli.append(stim)
            self.logger.info("Registered: BackgroundStimulus")

        if cfg.get("looming", {}).get("enabled", False):
            stim = LoomingStimulus(cfg.get("looming", {}), self._scene)
            stim.setup()
            self._stimuli.append(stim)
            self.logger.info(
                "Registered: LoomingStimulus (type=%s)",
                stim._expansion_type,
            )

        if cfg.get("oscillating_square", {}).get("enabled", False):
            stim = OscillatingSquare(cfg.get("oscillating_square", {}), self._scene)
            stim.setup()
            self._stimuli.append(stim)
            self.logger.info("Registered: OscillatingSquare")

    def _zmq_poll_task(self, task):
        """Non-blocking ZMQ poll -- runs every frame inside Panda3D task loop."""
        if self.stop_event.is_set():
            self._scene.finalizeExit()
            return Task.done

        if self._zmq_socket is None:
            return Task.cont

        try:
            while True:
                try:
                    parts = self._zmq_socket.recv_multipart(flags=zmq.NOBLOCK)
                except zmq.Again:
                    break
                topic = parts[0].decode()
                data = json.loads(parts[1])
                if topic == self._zone_enter_topic:
                    self._handle_zone_enter(data)
        except Exception:
            self.logger.exception("Error in ZMQ poll task")

        return Task.cont

    def _stimulus_update_task(self, task):
        """Per-frame stimulus animation update."""
        from panda3d.core import ClockObject

        dt = ClockObject.getGlobalClock().getDt()
        for stim in self._stimuli:
            try:
                stim.update(dt)
            except Exception:
                self.logger.exception(
                    "Error in stimulus %s update", type(stim).__name__
                )
        return Task.cont

    def _handle_zone_enter(self, data: dict) -> None:
        braid_rad = data.get("mean_heading", 0.0)
        world_heading = braid_to_world_heading(braid_rad, self._offset_rad, self._flip)
        self.logger.info(
            "ZONE_ENTER obj=%s world_heading=%.1f deg",
            data.get("obj_id"),
            world_heading,
        )

        stim_params: dict = {}
        for stim in self._stimuli:
            try:
                result = stim.on_trigger(world_heading, data)
                if result:
                    stim_params.update(result)
            except Exception:
                self.logger.exception(
                    "Error in stimulus %s on_trigger", type(stim).__name__
                )

        activated = bool(stim_params)

        # Only log a row (and thus only create stim.csv) when a stimulus
        # actually displayed something this trigger — e.g. BackgroundStimulus
        # is always-on and never returns params, so a background-only setup
        # produces no rows and no file, mirroring opto.csv's active gating.
        if self._csv_writer and activated:
            self._csv_writer.append(
                {
                    "timestamp": data.get("timestamp", time.time()),
                    "obj_id": data.get("obj_id"),
                    "frame": data.get("frame"),
                    "braid_heading_rad": braid_rad,
                    "world_heading_deg": world_heading,
                    **stim_params,
                }
            )

        self._publish_latency(data, activated)

    def _publish_latency(self, data: dict, activated: bool) -> None:
        """Publish one LATENCY message for the methods-paper latency log.

        activation_timestamp is stamped right after the on_trigger()
        dispatch loop above, not a true frame-presentation callback -- the
        render+vsync gap (roughly one frame) is not measured directly here;
        document it as an estimated constant in the paper instead.
        """
        if self._latency_socket is None:
            return
        timing = extract_trigger_timing(data)
        message = {
            "system": "visual",
            "obj_id": data.get("obj_id"),
            "frame": data.get("frame"),
            "braid_timestamp": timing.braid_timestamp,
            "trigger_timestamp": timing.handler_timestamp,
            "activation_timestamp": time.time() if activated else None,
            "sham": not activated,
        }
        try:
            self._latency_socket.send(json.dumps(message).encode("utf-8"))
        except Exception:
            self.logger.exception("Error publishing LATENCY message")
