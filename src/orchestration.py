"""Experiment orchestration: starts/stops/monitors the OptoFly worker process pool.

Extracted from main.py so both the CLI (main.py) and, in the future, other
front-ends (e.g. a GUI backend) can drive the same process lifecycle through
one interface instead of duplicating the spawn/shutdown sequence.
"""

import logging
import multiprocessing as mp
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Process classes used by start() to spawn the worker pool. Imported here
# (rather than inside start()) because tests/test_orchestration.py's
# patch_processes fixture monkeypatches these exact module attributes
# (monkeypatch.setattr requires the attribute to already exist).
from src.processes.braid import BraidPublisher
from src.processes.tracking import TriggerHandler
from src.processes.latency_logger import LatencyLogger
from src.visual.process import VisualProcess
from src.processes.led import OptoTriggerWorker
from src.processes.camera import RustCameraProcess as CameraProcess
from src.processes.lens import LiquidLens
from src.utils.braid import BraidProxy, check_braid_folder_exists
from src.utils.braid import verify_csv_files_in_braid
from src.utils.config import AppConfig
from src.utils.logger import configure_process_logging
from src.utils.metadata import append_metadata_to_csv
from src.utils.metadata import extract_config_columns
from src.utils.metadata import write_metadata

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


def _critical_names(app_config: AppConfig) -> set[str]:
    """Return the processes whose death is fatal *for this config*.

    Criticality is config-dependent rather than fixed, because not every
    process the pool spawns is one the experiment depends on:

    - OptoTriggerWorker is started unconditionally (it also drives the
      backlight), but with `opto_trigger.active = false` there is no
      stimulation to lose. Treating its death as fatal there meant a rig with
      no Arduino attached could not start at all -- even though the shipped
      example config disables stimulation.
    - LiquidLens is only spawned when `camera.active`, so it cannot be
      critical when the camera is off.

    BraidPublisher and TriggerHandler are always critical: without tracking
    or zone events there is no experiment, whatever else is configured.
    """
    names = {"BraidPublisher", "TriggerHandler"}
    if app_config.opto_trigger.active:
        names.add("OptoTriggerWorker")
    if app_config.camera.active:
        names.add("LiquidLens")
    return names


def _check_critical_processes_alive(
    processes: list,
    critical: set[str] | None = None,
    reported_reasons: dict[str, str] | None = None,
) -> list[str]:
    """Return one FATAL message per critical process that has died.

    `processes` is a list of (name, process) tuples. Only names in `critical`
    (default: every name in _CRITICAL_INIT_HINTS) are checked -- everything
    else (VisualProcess, CameraProcess, LatencyLogger) dying is not fatal
    here. Callers that have an AppConfig should pass
    _critical_names(app_config) so config-disabled subsystems are excluded.

    `reported_reasons` maps process name to the exception the child itself
    reported through its failure queue. That always beats the static hint:
    LiquidLens dies on a missing or malformed calibrations/liquid_lens.csv at
    least as often as on a bad serial port, and telling someone to check the
    hardware connection sends them to unplug a working lens. The hints remain
    the fallback for processes that exit without raising.
    """
    if critical is None:
        critical = set(_CRITICAL_INIT_HINTS)
    reported_reasons = reported_reasons or {}
    messages = []
    for name, proc in processes:
        if name in critical and not proc.is_alive():
            detail = reported_reasons.get(name) or _CRITICAL_INIT_HINTS[name]
            messages.append(f"{name} process exited during initialization. {detail}")
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
        # Filled by workers that crash in _run(); drained by _drain_failures()
        # so a fatal message can quote the child's real exception instead of a
        # static per-process hint.
        self._failure_queue: Optional[Any] = None
        self._reported_reasons: dict[str, str] = {}
        # Which process names are fatal for the run currently configured;
        # populated by start() via _critical_names(). Empty before start().
        self._critical: set[str] = set()

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

    @property
    def braid_proxy(self) -> Optional[BraidProxy]:
        """The BraidProxy from the most recent prepare_braid_folder()/start()
        call, or None. Exposed for callers (main.py's metadata-cancellation
        path) that need to stop a recording that was started before start()
        was ever called."""
        return self._braid_proxy

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

    def start(self, config_path: str, metadata: Optional[dict] = None) -> None:
        if self.is_running():
            raise ExperimentAlreadyRunningError("Experiment already running")

        app_config = AppConfig.load(config_path)
        log_level_str = app_config.logging.level
        log_level_int = app_config.logging.level_int()

        if self._braid_folder is None:
            self.prepare_braid_folder(config_path)
        braid_folder = self._braid_folder

        if metadata is not None:
            write_metadata(metadata, braid_folder)
            config_columns = extract_config_columns(config_path)
            append_metadata_to_csv(metadata, braid_folder, config_columns)
        experiment_duration = (
            float(metadata.get("experiment_duration", 24)) if metadata else 24.0
        )
        self._end_time = datetime.now().timestamp() + experiment_duration * 3600

        _copy_config_to_braid_folder(config_path, braid_folder)
        if app_config.visual_stimuli.active:
            _copy_config_to_braid_folder(
                app_config.visual_stimuli.config_file, braid_folder
            )

        log_path = str(Path(braid_folder) / "optofly.log")
        self._log_path = log_path
        configure_process_logging(log_path, "Main", "WHITE", level=log_level_int)
        logger.info(f"Logging to: {log_path}")

        stop_event = mp.Event()
        self._stop_event = stop_event
        self._processes = []
        self._failed_reasons = {}
        self._shutdown_state = {}
        self._known_dead = set()
        self._critical = _critical_names(app_config)
        self._failure_queue = mp.Queue()
        self._reported_reasons = {}

        common = dict(
            config_path=config_path,
            event=stop_event,
            log_path=log_path,
            log_level=log_level_str,
            failure_queue=self._failure_queue,
        )

        # ── Experiment configuration summary ──
        print("\n" + "=" * 70)
        print("OptoFly Experiment Configuration")
        print("=" * 70)
        print("\nActive Processes:")
        print("  ✓ BraidPublisher")
        print("  ✓ TriggerHandler")
        print("  ✓ LatencyLogger")
        if app_config.visual_stimuli.active:
            print("  ✓ VisualProcess (Panda3D)")
        if app_config.camera.active:
            print("  ✓ CameraProcess")
            print("  ✓ LiquidLens")
        print("  ✓ OptoTriggerWorker")
        if app_config.camera.active:
            print(
                f"\nCamera: {app_config.camera.resolution[0]}×{app_config.camera.resolution[1]} @ {app_config.camera.fps}fps"
            )
        if app_config.camera.active:
            predictor = app_config.liquid_lens.predictor
            lens_detail = f"diopter, {predictor} predictor"
            if predictor == "linear":
                lens_detail += f" (latency={app_config.liquid_lens.system_latency}s, horizon={app_config.liquid_lens.prediction_horizon}s)"
            print(f"Liquid Lens: {lens_detail}")
        if app_config.opto_trigger.active:
            dur = app_config.opto_trigger.duration
            if isinstance(dur, list):
                dur = f"[{', '.join(str(d) for d in dur)}]"
            print(
                f"\nOpto Trigger: {app_config.opto_trigger.color}, {app_config.opto_trigger.intensity}/255, {dur}ms"
            )
        if app_config.visual_stimuli.active:
            enabled_stimuli = []
            try:
                with open(app_config.visual_stimuli.config_file, "rb") as f:
                    vs_data = tomllib.load(f).get("visual_stimuli", {})
                for section_name, section in vs_data.items():
                    if isinstance(section, dict) and section.get("enabled", False):
                        name = section_name.replace("_", " ").capitalize()
                        if section_name == "looming":
                            exp_type = section.get("type", "exponential")
                            name += f" (type={exp_type})"
                        enabled_stimuli.append(name)
            except Exception:
                pass
            if enabled_stimuli:
                print(f"\nVisual Stimuli: {', '.join(enabled_stimuli)}")
        print("\n" + "=" * 70 + "\n")
        # ── end config summary ──

        logger.info("Starting core processes...")
        braid_publisher = BraidPublisher(**common)
        braid_publisher.start()
        self._processes.append(("BraidPublisher", braid_publisher))
        logger.info("  ✓ BraidPublisher")
        time.sleep(0.5)

        trigger_handler = TriggerHandler(**common)
        trigger_handler.start()
        self._processes.append(("TriggerHandler", trigger_handler))
        logger.info("  ✓ TriggerHandler")
        time.sleep(0.5)

        latency_logger = LatencyLogger(
            config_path=config_path,
            event=stop_event,
            braid_folder=braid_folder,
            log_path=log_path,
            log_level=log_level_str,
        )
        latency_logger.start()
        self._processes.append(("LatencyLogger", latency_logger))
        logger.info("  ✓ LatencyLogger")
        time.sleep(0.5)

        logger.info("Starting optional processes...")

        if app_config.visual_stimuli.active:
            visual_process = VisualProcess(
                config_path=config_path,
                event=stop_event,
                braid_folder=braid_folder,
                log_path=log_path,
                log_level=log_level_str,
            )
            visual_process.start()
            self._processes.append(("VisualProcess", visual_process))
            logger.info("  ✓ VisualProcess (Panda3D)")

        if app_config.camera.active:
            video_folder = str(
                Path(braid_folder).parent.parent / "videos" / Path(braid_folder).name
            )
            camera = CameraProcess(
                config_path=config_path,
                event=stop_event,
                save_folder=video_folder,
                log_path=log_path,
                log_level=log_level_str,
            )
            camera.start()
            self._processes.append(("CameraProcess", camera))
            logger.info("  ✓ CameraProcess")

            liquid_lens = LiquidLens(
                event=stop_event,
                config_path=config_path,
                braid_folder=braid_folder,
                video_folder=video_folder,
                log_path=log_path,
                log_level=log_level_str,
            )
            liquid_lens.start()
            self._processes.append(("LiquidLens", liquid_lens))
            logger.info("  ✓ LiquidLens")

        opto_trigger = OptoTriggerWorker(
            event=stop_event,
            braid_folder=braid_folder,
            config_path=config_path,
            log_path=log_path,
            log_level=log_level_str,
        )
        opto_trigger.start()
        self._processes.append(("OptoTriggerWorker", opto_trigger))
        logger.info("  ✓ OptoTriggerWorker")

        time.sleep(1)

        self._drain_failures()
        fatal_messages = _check_critical_processes_alive(
            self._processes, self._critical, self._reported_reasons
        )
        if fatal_messages:
            for name, proc in self._processes:
                if name in self._critical and not proc.is_alive():
                    detail = (
                        self._reported_reasons.get(name) or _CRITICAL_INIT_HINTS[name]
                    )
                    self._failed_reasons[name] = (
                        f"{name} process exited during initialization. {detail}"
                    )
            stop_event.set()
            raise ExperimentStartError("; ".join(fatal_messages))

    def stop(self) -> None:
        """Shut down the process pool and end the Braid recording.

        The two are deliberately independent. prepare_braid_folder() starts a
        recording before start() ever runs, so an exception anywhere in start()
        before the stop event is assigned would previously return at the guard
        below and never reach the braid_proxy teardown -- leaving Braid
        recording indefinitely with nobody to stop it, and the next run
        starting a second recording alongside it. Safe to call repeatedly and
        safe to call having started nothing.
        """
        if self._stop_event is not None:
            self._stop_event.set()
            time.sleep(1)

            for name, process in self._processes:
                if process.is_alive():
                    timeout = _SHUTDOWN_TIMEOUTS.get(name, 5)
                    logger.info(f"  Waiting for {name} to terminate...")
                    process.join(timeout=timeout)
                    if process.is_alive():
                        logger.info(f"  Force terminating {name}...")
                        process.terminate()
                        process.join(timeout=2)
                        self._shutdown_state[name] = "forced"
                    else:
                        self._shutdown_state[name] = "clean"
                else:
                    self._shutdown_state[name] = "clean"

            if self._braid_folder:
                verify_csv_files_in_braid(self._braid_folder)

        if self._braid_proxy is not None:
            logger.info("Stopping Braid recording...")
            try:
                self._braid_proxy.stop_csv_recording()
                logger.info("✓ Recording stopped")
            except Exception:
                logger.exception("Failed to stop Braid recording")

        self._stop_event = None
        self._braid_folder = None
        self._braid_proxy = None

    def _drain_failures(self) -> None:
        """Collect whatever crashing workers have reported since the last check.

        Non-blocking and tolerant: a worker may die without reporting (killed,
        or exiting via `return` rather than raising), in which case the static
        hint stays the fallback.
        """
        if self._failure_queue is None:
            return
        while True:
            try:
                name, reason = self._failure_queue.get_nowait()
            except Exception:
                return
            self._reported_reasons[name] = reason

    def check_health(self) -> None:
        """Passive/fatal mid-run health check -- call this once per iteration
        of the caller's wait loop.

        Critical processes (see _CRITICAL_INIT_HINTS) dying mid-run is fatal,
        matching main.py's original inline behavior: this sets the internal
        stop event so is_running() flips false on the caller's next check.

        Every other process dying mid-run is purely observational: logs once
        (not repeatedly) the first time it's noticed, never affects control
        flow. Nothing currently detects this at all, so this closes a real
        gap -- a non-critical process dying at hour 12 of a 24-hour
        experiment previously went unnoticed until someone looked.
        """
        if self._stop_event is None or self._stop_event.is_set():
            return

        self._drain_failures()
        fatal_messages = _check_critical_processes_alive(
            self._processes, self._critical, self._reported_reasons
        )
        if fatal_messages:
            for name, proc in self._processes:
                if name in self._critical and not proc.is_alive():
                    detail = (
                        self._reported_reasons.get(name) or _CRITICAL_INIT_HINTS[name]
                    )
                    self._failed_reasons[name] = (
                        f"{name} process exited during the run. {detail}"
                    )
                    logger.error(self._failed_reasons[name])
            self._stop_event.set()
            return

        for name, proc in self._processes:
            if name in self._critical:
                continue
            if name in self._known_dead:
                continue
            if not proc.is_alive():
                self._known_dead.add(name)
                reason = self._reported_reasons.get(name)
                logger.warning(
                    f"{name} process exited during the run (non-critical -- "
                    "experiment continues, but this subsystem is no longer active)."
                    + (f" Reason: {reason}" if reason else "")
                )
