"""Experiment orchestration: starts/stops/monitors the OptoFly worker process pool.

Extracted from main.py so both the CLI (main.py) and, in the future, other
front-ends (e.g. a GUI backend) can drive the same process lifecycle through
one interface instead of duplicating the spawn/shutdown sequence.
"""

import logging
import multiprocessing as mp
import time  # noqa: F401 -- used by start()/stop() (Tasks 3-4); monkeypatched in tests
from datetime import datetime  # noqa: F401 -- used by start() (Task 3) for end_time
from pathlib import Path
from typing import Any, Optional

# The process classes and helpers below are not yet called from this module --
# start()/stop()/check_health() land in Tasks 3-5 and will use them. They stay
# imported now because tests/test_orchestration.py's patch_processes fixture
# monkeypatches these exact module attributes (monkeypatch.setattr requires
# the attribute to already exist).
from src.processes.braid import BraidPublisher  # noqa: F401
from src.processes.tracking import TriggerHandler  # noqa: F401
from src.processes.latency_logger import LatencyLogger  # noqa: F401
from src.visual.process import VisualProcess  # noqa: F401
from src.processes.led import OptoTriggerWorker  # noqa: F401
from src.processes.camera import RustCameraProcess as CameraProcess  # noqa: F401
from src.processes.lens import LiquidLens  # noqa: F401
from src.utils.braid import BraidProxy, check_braid_folder_exists
from src.utils.braid import verify_csv_files_in_braid  # noqa: F401
from src.utils.config import AppConfig
from src.utils.logger import configure_process_logging  # noqa: F401
from src.utils.metadata import append_metadata_to_csv  # noqa: F401
from src.utils.metadata import extract_config_columns  # noqa: F401
from src.utils.metadata import write_metadata  # noqa: F401
from src.monitoring.server import run_server  # noqa: F401

logger = logging.getLogger(__name__)
# Always emit this module's milestone messages at INFO regardless of the root
# logger's configured level (set from config.toml's [logging] level, which may
# be WARNING or higher). Scoped to this logger only -- worker processes still
# follow the root's configured level as before.
logger.setLevel(logging.INFO)

_SHUTDOWN_TIMEOUTS = {"CameraProcess": 35, "RustCamera": 35}

# Processes that exit immediately and unrecoverably on their own init failure
# (bad serial port, unreachable Braid server, ZMQ bind conflict) rather than
# retrying in the background -- safe to treat as fatal. Each gets its own
# diagnostic hint so a BraidPublisher connectivity failure is never
# misattributed to the lens or opto hardware.
_CRITICAL_INIT_HINTS = {
    "LiquidLens": "Check hardware connection and the relevant port in config.toml.",
    "OptoTriggerWorker": "Check hardware connection and the relevant port in config.toml.",
    "BraidPublisher": "Check that Braid is running and reachable at the configured host/port in config.toml.",
    "TriggerHandler": "Check that the ZMQ trigger_port in config.toml is not already in use by another process.",
}


class ExperimentAlreadyRunningError(Exception):
    """Raised by start() when an experiment is already running on this instance."""


class ExperimentStartError(Exception):
    """Raised when a critical process (see _CRITICAL_INIT_HINTS) fails to come
    up during start(). The experiment's processes are left for the caller to
    shut down via stop() -- this mirrors main.py's original behavior where the
    FATAL check still ran through the finally-block shutdown sequence rather
    than exiting immediately."""


def _check_critical_processes_alive(processes: list) -> list[str]:
    """Return one FATAL message per critical process that has died.

    `processes` is a list of (name, process) tuples. Only names in
    _CRITICAL_INIT_HINTS are checked -- everything else (Monitoring Server,
    VisualProcess, CameraProcess, LatencyLogger) dying is not fatal here.
    """
    messages = []
    for name, proc in processes:
        if name in _CRITICAL_INIT_HINTS and not proc.is_alive():
            messages.append(f"{name} process exited. {_CRITICAL_INIT_HINTS[name]}")
    return messages


def _copy_config_to_braid_folder(config_path: str, braid_folder: str) -> None:
    config_src = Path(config_path)
    config_dest = Path(braid_folder) / config_src.name
    try:
        with open(config_src, "rb") as src_file, open(config_dest, "wb") as dest_file:
            dest_file.write(src_file.read())
    except Exception:
        logger.exception("Failed to copy %s into %s", config_path, braid_folder)


class Experiment:
    """Owns the lifecycle of one OptoFly experiment run.

    main.py (CLI) constructs one Experiment and drives it through
    prepare_braid_folder() / start() / stop() / status() / check_health() --
    the process-spawning sequence itself lives here exactly once, so a
    future front-end (e.g. a GUI backend) can reuse it without duplicating
    main.py's process-management logic.
    """

    def __init__(self):
        self._processes: list[tuple[str, Any]] = []
        self._stop_event: Optional[mp.Event] = None
        self._braid_folder: Optional[str] = None
        self._braid_proxy: Optional[BraidProxy] = None
        self._end_time: Optional[float] = None
        self._log_path: Optional[str] = None
        self._failed_reasons: dict[str, str] = {}
        self._shutdown_state: dict[str, str] = {}
        self._known_dead: set[str] = set()

    def is_running(self) -> bool:
        return self._stop_event is not None and not self._stop_event.is_set()

    def needs_cleanup(self) -> bool:
        """True when a stop has been signaled (by anything -- a worker process
        crashing, check_health() detecting a critical failure, the shared
        mp.Event being set directly, etc.) but stop() hasn't yet run to
        actually join/terminate processes and reset state.

        The CLI (main.py) always calls experiment.stop() in a finally block,
        so a mid-run crash there still gets cleaned up. A future long-running
        front-end has no such wrapper; callers should poll this and call
        stop() when it goes True.
        """
        return self._stop_event is not None and self._stop_event.is_set()

    def status(self) -> dict:
        processes = {}
        for name, proc in self._processes:
            processes[name] = {
                "alive": proc.is_alive(),
                "failed_reason": self._failed_reasons.get(name),
                "shutdown": self._shutdown_state.get(name),
            }
        return {
            "running": self.is_running(),
            "braid_folder": self._braid_folder,
            "end_time": self._end_time,
            "log_path": self._log_path,
            "processes": processes,
        }

    def prepare_braid_folder(self, config_path: str) -> str:
        """Confirm/start Braid recording and return the resulting folder path.

        Call this before collecting metadata so the researcher can see where
        data will be saved ahead of time. start() reuses the result instead
        of checking again.

        Raises:
            BraidFolderError: see check_braid_folder_exists().
        """
        app_config = AppConfig.load(config_path)
        self._braid_folder, self._braid_proxy = check_braid_folder_exists(
            app_config.braid_publisher.experiments_path,
            callback_url=app_config.braid_publisher.callback_url,
            auto_start_recording=True,
        )
        return self._braid_folder

    def stop(self) -> None:
        """No-op when nothing has been started yet.

        main.py's finally block always calls stop(), including when start()
        was never reached (e.g. prepare_braid_folder() raised) or failed
        before an mp.Event was created -- this guard makes that safe. The
        real shutdown sequence (join/terminate processes, reset state) is
        added in Task 4 alongside start().
        """
        if self._stop_event is None:
            return
