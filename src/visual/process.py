"""Panda3D visual stimuli process for OptoFly.

Subscribes to ZONE_ENTER messages via ZMQ, converts Braid heading to
world degrees, dispatches to registered stimuli, and drives the Panda3D
render loop via the task manager.
"""

import json
import math
import multiprocessing as mp
import time
import tomllib
from pathlib import Path
from typing import Optional

import zmq
from direct.task import Task

from src.utils.worker import WorkerProcess
from src.utils.csv_writer import CSVWriter
from src.visual.scene import ArenaScene
from src.visual.stimuli.background import BackgroundStimulus
from src.visual.stimuli.looming import LoomingStimulus


def braid_to_world_heading(
    braid_rad: float, offset_rad: float, flip: bool
) -> float:
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

        self._setup_zmq()
        self._csv_writer = self._setup_csv(cfg)

        self._scene = ArenaScene(
            viewing_distance_cm=arena_cfg.get("viewing_distance_cm", 25.0),
            standalone=self.standalone,
        )

        self._stimuli = []
        self._initialize_stimuli(cfg)

        self._scene.taskMgr.add(self._zmq_poll_task, "zmq_poll", sort=0)
        self._scene.taskMgr.add(
            self._stimulus_update_task, "stimulus_update", sort=1
        )

        self._scene.run()  # blocks until finalizeExit() is called

        self._close_csv()
        if self._zmq_context:
            self._zmq_context.term()

    def _load_config(self) -> dict:
        with open(self._config_path, "rb") as f:
            main = tomllib.load(f)
        vs_path = Path(
            main.get("visual_stimuli", {}).get(
                "config_file", "configs/visual_stimuli.toml"
            )
        )
        if vs_path.exists():
            with open(vs_path, "rb") as f:
                raw = tomllib.load(f)
        else:
            raw = main
        return raw.get("visual_stimuli", {})

    def _setup_zmq(self) -> None:
        self._zmq_context = None
        self._zmq_socket = None
        if self.standalone:
            return
        self._zmq_context = zmq.Context()
        self._zmq_socket = self._zmq_context.socket(zmq.SUB)
        self._zmq_socket.connect("tcp://localhost:5556")
        self._zmq_socket.setsockopt_string(zmq.SUBSCRIBE, "ZONE_ENTER")

    def _setup_csv(self, cfg: dict) -> Optional[CSVWriter]:
        log_file = cfg.get("log_file", "stim.csv")
        path = (
            str(Path(self.braid_folder) / log_file)
            if self.braid_folder
            else log_file
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

        if cfg.get("looming", {}).get("enabled", False):
            stim = LoomingStimulus(cfg.get("looming", {}), self._scene)
            stim.setup()
            self._stimuli.append(stim)

    def _zmq_poll_task(self, task):
        """Non-blocking ZMQ poll -- runs every frame inside Panda3D task loop."""
        if self.stop_event.is_set():
            self._scene.finalizeExit()
            return Task.done

        if self._zmq_socket is None:
            return Task.cont

        try:
            while True:
                parts = self._zmq_socket.recv_multipart(flags=zmq.NOBLOCK)
                topic = parts[0].decode()
                data = json.loads(parts[1])
                if topic == "ZONE_ENTER":
                    self._handle_zone_enter(data)
        except zmq.Again:
            pass

        return Task.cont

    def _stimulus_update_task(self, task):
        """Per-frame stimulus animation update."""
        from panda3d.core import ClockObject

        dt = ClockObject.getGlobalClock().getDt()
        for stim in self._stimuli:
            stim.update(dt)
        return Task.cont

    def _handle_zone_enter(self, data: dict) -> None:
        braid_rad = data.get("mean_heading", 0.0)
        world_heading = braid_to_world_heading(
            braid_rad, self._offset_rad, self._flip
        )

        for stim in self._stimuli:
            stim.on_trigger(world_heading, data)

        if self._csv_writer:
            self._csv_writer.append(
                {
                    "timestamp": data.get("timestamp", time.time()),
                    "obj_id": data.get("obj_id"),
                    "braid_heading_rad": braid_rad,
                    "world_heading_deg": world_heading,
                }
            )
