"""
Camera process module — launches the Rust optofly-camera binary for
triggered high-speed video capture.
"""

import multiprocessing as mp
import os
import re
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.utils.config import AppConfig
from src.utils.worker import WorkerProcess

BINARY_NAME = "optofly-camera"

# env_logger's own "[2026-08-25T23:03:06Z WARN  optofly_camera::capture]"
# prefix duplicates the outer Python log line's timestamp; strip it so
# forwarded lines read as "[optofly-camera] WARN capture: <message>"
# instead of two nested bracketed/timestamped headers.
_RUST_LOG_PREFIX_RE = re.compile(
    r"^\[\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+(\w+)\s+([^\]]+)\]\s*(.*)$"
)


def find_camera_binary() -> str:
    """Locate the optofly-camera binary.

    Raises:
        FileNotFoundError: with the build command, when it isn't there.
    """
    project_root = Path(__file__).parent.parent.parent
    candidates = [
        project_root / "optofly-camera" / "target" / "release" / BINARY_NAME,
        project_root / "optofly-camera" / "target" / "debug" / BINARY_NAME,
    ]
    for path in candidates:
        if path.exists():
            return str(path)

    found = shutil.which(BINARY_NAME)
    if found:
        return found

    raise FileNotFoundError(
        f"Cannot find {BINARY_NAME}. "
        f"Build with: cd optofly-camera && cargo build --release"
    )


@dataclass
class CheckResult:
    """One preflight check: whether it passed, and what to do if it didn't."""

    ok: bool
    detail: str

    def __repr__(self) -> str:  # printed directly by the documented usage
        return f"{'✓' if self.ok else '✗'} {self.detail}"


def _check_ffmpeg() -> CheckResult:
    path = shutil.which("ffmpeg")
    if path:
        return CheckResult(True, f"ffmpeg found at {path}")
    return CheckResult(
        False,
        "ffmpeg is not on PATH — video encoding will fail. "
        "Install it with: sudo apt-get install -y ffmpeg",
    )


def _check_camera_binary() -> CheckResult:
    try:
        return CheckResult(True, f"{BINARY_NAME} found at {find_camera_binary()}")
    except FileNotFoundError:
        return CheckResult(
            False,
            f"{BINARY_NAME} is not built — the camera cannot record. "
            "Build it with: cd optofly-camera && cargo build --release",
        )


def _check_save_folder(save_folder: str) -> CheckResult:
    """Could the camera create this folder and write into it?

    The folder not existing yet is fine -- the camera creates it on start.
    Deliberately creates nothing itself: a preflight check with side effects
    left a stray camera_videos/ behind every time it ran. Tests the nearest
    existing ancestor instead, which is exactly what os.makedirs() needs
    write access to.
    """
    target = Path(save_folder).expanduser()
    ancestor = target
    while not ancestor.exists() and ancestor.parent != ancestor:
        ancestor = ancestor.parent

    if not ancestor.is_dir():
        return CheckResult(
            False,
            f"save folder {save_folder} cannot be created: {ancestor} is a file, "
            "not a directory.",
        )
    if not os.access(ancestor, os.W_OK | os.X_OK):
        return CheckResult(
            False,
            f"save folder {save_folder} is not writable — no write permission on "
            f"{ancestor}. Check you own that path and the disk is not full.",
        )
    if target == ancestor:
        return CheckResult(True, f"save folder {save_folder} exists and is writable")
    return CheckResult(
        True, f"save folder {save_folder} does not exist yet but can be created"
    )


def _check_trigger_port(port: int) -> CheckResult:
    """Report whether TriggerHandler is currently publishing.

    A free port is not a fault to fix -- it just means the experiment isn't
    running yet -- so the wording has to say so.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return CheckResult(
                True, f"something is publishing on trigger port {port} (stack is up)"
            )
    return CheckResult(
        False,
        f"nothing is bound to trigger port {port} — the experiment is not running. "
        "Expected unless main.py is live; start it and re-run this check.",
    )


def check_camera_prerequisites(
    config_path: str = "configs/config.toml",
    save_folder: Optional[str] = None,
) -> dict[str, CheckResult]:
    """Preflight the four things that stop the camera from recording.

    Args:
        config_path: Path to the TOML config.
        save_folder: Override the folder to test for writability. Defaults to
            the configured ``camera.save_folder``.

    Returns:
        Mapping of check name to CheckResult. Print it directly:

        >>> from src.processes.camera import check_camera_prerequisites
        >>> print(check_camera_prerequisites("configs/config.toml"))
    """
    app_config = AppConfig.load(config_path)
    return {
        "camera_binary": _check_camera_binary(),
        "ffmpeg": _check_ffmpeg(),
        "save_folder_writable": _check_save_folder(
            save_folder if save_folder is not None else app_config.camera.save_folder
        ),
        "trigger_port": _check_trigger_port(app_config.zmq.trigger_port),
    }


class RustCameraProcess(WorkerProcess):
    """
    Camera process that launches the Rust optofly-camera binary as a subprocess.
    """

    BINARY_NAME = BINARY_NAME

    def __init__(
        self,
        config_path: str = "configs/config.toml",
        event: Optional[mp.Event] = None,
        save_folder: Optional[str] = None,
        process_name: str = "RustCamera",
        log_level: str = "INFO",
        log_color: str = "CYAN",
        log_path: str | None = None,
    ):
        super().__init__(
            event=event,
            log_path=log_path,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        self.config_path = config_path
        self.save_folder = save_folder or AppConfig.load(config_path).camera.save_folder
        self.stop_event = event if event is not None else mp.Event()
        self._proc: Optional[subprocess.Popen] = None

    def _find_binary(self) -> str:
        """Locate the optofly-camera binary (see find_camera_binary)."""
        return find_camera_binary()

    def _run(self) -> None:
        """Launch the Rust binary and wait for it to finish."""
        self.logger.info("Starting RustCameraProcess")

        try:
            binary = self._find_binary()
        except FileNotFoundError:
            self.logger.error(
                "\n"
                "  ╔══════════════════════════════════════════════════════╗\n"
                "  ║  CAMERA BINARY NOT FOUND — camera will not record   ║\n"
                "  ║                                                      ║\n"
                "  ║  Build it first:                                     ║\n"
                "  ║    cd optofly-camera && cargo build --release        ║\n"
                "  ╚══════════════════════════════════════════════════════╝"
            )
            return

        os.makedirs(self.save_folder, exist_ok=True)

        cmd = [
            binary,
            "--config",
            self.config_path,
            "--save-folder",
            self.save_folder,
            "--log-level",
            "warn",
        ]
        self.logger.info("Launching: %s", " ".join(cmd))

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        output_thread = threading.Thread(
            target=self._forward_output,
            daemon=True,
        )
        output_thread.start()

        while not self.stop_event.is_set():
            try:
                self._proc.wait(timeout=0.5)
                if self._proc.returncode != 0:
                    self.logger.error(
                        "optofly-camera exited with code %d",
                        self._proc.returncode,
                    )
                else:
                    self.logger.info("optofly-camera exited cleanly")
                return
            except subprocess.TimeoutExpired:
                continue

        self.logger.info("Sending SIGTERM to optofly-camera")
        self._proc.terminate()
        try:
            self._proc.wait(timeout=30.0)
            self.logger.info("optofly-camera exited after SIGTERM")
        except subprocess.TimeoutExpired:
            self.logger.error("optofly-camera did not exit after SIGTERM, killing")
            self._proc.kill()

    _XIAPI_NOISE_PATTERNS = (
        "xiAPI: EAL_IF",
        "xiAPI: FGTL_SetParam_to_CAL",
        "xiAPI: SAL_Common_SetAcquisitionFrameRate",
        "xiAPI: xiFAPI_Device::AllocateBuffers",
        "xiAPI: Bandwidth measurement",
    )

    def _forward_output(self) -> None:
        """Read from Rust binary stdout/stderr and forward to logger (daemon thread)."""
        try:
            if self._proc and self._proc.stdout:
                for line in self._proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    if any(pattern in line for pattern in self._XIAPI_NOISE_PATTERNS):
                        continue
                    match = _RUST_LOG_PREFIX_RE.match(line)
                    if match:
                        level, target, rest = match.groups()
                        target = target.rsplit("::", 1)[-1]
                        line = f"{level} {target}: {rest}"
                    self.logger.info("[optofly-camera] %s", line)
        except Exception as e:
            self.logger.warning("Error reading optofly-camera output: %s", e)
