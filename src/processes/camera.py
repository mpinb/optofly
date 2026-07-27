"""
Camera process module — launches the Rust optofly-camera binary for
triggered high-speed video capture.
"""

import multiprocessing as mp
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from src.utils.config import AppConfig
from src.utils.worker import WorkerProcess


class RustCameraProcess(WorkerProcess):
    """
    Camera process that launches the Rust optofly-camera binary as a subprocess.
    """

    BINARY_NAME = "optofly-camera"

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
        """Locate the optofly-camera binary."""
        project_root = Path(__file__).parent.parent.parent
        candidates = [
            project_root / "optofly-camera" / "target" / "release" / self.BINARY_NAME,
            project_root / "optofly-camera" / "target" / "debug" / self.BINARY_NAME,
        ]
        for path in candidates:
            if path.exists():
                return str(path)

        found = shutil.which(self.BINARY_NAME)
        if found:
            return found

        raise FileNotFoundError(
            f"Cannot find {self.BINARY_NAME}. "
            f"Build with: cd optofly-camera && cargo build --release"
        )

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

    def _forward_output(self) -> None:
        """Read from Rust binary stdout/stderr and forward to logger (daemon thread)."""
        try:
            if self._proc and self._proc.stdout:
                for line in self._proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.logger.info("[optofly-camera] %s", line)
        except Exception as e:
            self.logger.warning("Error reading optofly-camera output: %s", e)
