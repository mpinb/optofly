"""
Ximea Camera Process module for triggered video recording.

This module manages the Rust-based ximea_camera binary as a subprocess,
providing integration with the OptoFly trigger system.
"""

import multiprocessing as mp
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from src.utils.config import CameraConfig
from src.utils.worker import WorkerProcess


def check_camera_prerequisites(config_path: str = "configs/config.toml") -> dict:
    """
    Run pre-flight checks for the camera system.

    Args:
        config_path: Path to the configuration file

    Returns:
        Dictionary with check results:
        {
            "rust_binary": bool,
            "ffmpeg": bool,
            "save_folder": bool,
            "zmq_port": bool,
            "overall": bool,
            "errors": list[str],
            "warnings": list[str]
        }
    """
    config = CameraConfig(config_path)
    results = {
        "rust_binary": False,
        "ffmpeg": False,
        "save_folder": False,
        "zmq_port": False,
        "overall": False,
        "errors": [],
        "warnings": [],
    }

    # Check 1: Rust binary exists and is executable
    binary_path = Path(config.rust_binary)
    if not binary_path.is_absolute():
        binary_path = Path.cwd() / binary_path

    if not binary_path.exists():
        results["errors"].append(f"Camera binary not found: {binary_path}")
        results["errors"].append(
            "Run: cd rust/ximea_camera && cargo build --release"
        )
    elif not os.access(binary_path, os.X_OK):
        results["errors"].append(f"Camera binary is not executable: {binary_path}")
    else:
        results["rust_binary"] = True

    # Check 2: FFmpeg with NVENC support
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        results["errors"].append("FFmpeg not found in PATH")
        results["errors"].append("Install: sudo apt-get install ffmpeg")
    else:
        # Check for NVENC encoder
        try:
            proc = subprocess.run(
                ["ffmpeg", "-encoders"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "h264_nvenc" in proc.stdout:
                results["ffmpeg"] = True
            else:
                results["warnings"].append(
                    "FFmpeg found but h264_nvenc encoder not available"
                )
                results["warnings"].append(
                    "Video encoding will be slower without GPU acceleration"
                )
                results["ffmpeg"] = True  # Still usable
        except Exception as e:
            results["warnings"].append(f"Could not check FFmpeg encoders: {e}")
            results["ffmpeg"] = True  # Assume it will work

    # Check 3: Save folder is writable
    save_path = Path(config.save_folder)
    if not save_path.is_absolute():
        config_dir = Path(config_path).parent
        save_path = (config_dir / save_path).resolve()

    try:
        save_path.mkdir(parents=True, exist_ok=True)
        # Try to create a test file
        test_file = save_path / ".write_test"
        test_file.touch()
        test_file.unlink()
        results["save_folder"] = True
    except Exception as e:
        results["errors"].append(f"Save folder not writable: {save_path}")
        results["errors"].append(f"Error: {e}")

    # Check 4: ZMQ port is available
    try:
        # Try to bind to the port to check if it's available
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((config.zmq_address, int(config.zmq_port)))
        sock.close()

        if result == 0:
            # Port is already in use (connection succeeded)
            results["warnings"].append(
                f"ZMQ port {config.zmq_port} is already in use"
            )
            results["warnings"].append(
                "The camera will connect to existing publisher"
            )
        results["zmq_port"] = True  # Not necessarily an error
    except Exception as e:
        results["warnings"].append(f"Could not check ZMQ port: {e}")
        results["zmq_port"] = True  # Assume it will work

    # Overall result
    results["overall"] = (
        results["rust_binary"]
        and results["ffmpeg"]
        and results["save_folder"]
        and results["zmq_port"]
    )

    return results


class CameraProcess(WorkerProcess):
    """
    Process wrapper for the Ximea camera recording system.

    This class manages the lifecycle of the Rust ximea_camera binary,
    which handles high-speed triggered video recording with ring buffer.
    """

    def __init__(
        self,
        config_path: str = "configs/config.toml",
        event: Optional[mp.Event] = None,
        save_folder: Optional[str] = None,
        process_name: str = "CameraProcess",
        log_level: str = "INFO",
        log_color: str = "CYAN",
    ):
        """
        Initialize the CameraProcess.

        Args:
            config_path: Path to the configuration file
            event: Event to signal process termination (created if None)
            save_folder: Override save folder path (defaults to config value)
            process_name: Name to display in logs
            log_level: Logging level to use
            log_color: Color for log messages
        """
        # Pass parameters to parent class
        super().__init__(
            event=event,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        # Load configuration
        self.config = CameraConfig(config_path)
        if save_folder is not None:
            self.config.save_folder = save_folder
        self.config_path = config_path
        self.stop_event = event if event is not None else mp.Event()
        self.is_initialized = False

        # Subprocess reference
        self.camera_subprocess: Optional[subprocess.Popen] = None

        # Initialize logger
        self._initialize_logger()
        self.logger.info(f"Initializing CameraProcess with config: {config_path}")

    def _build_command_args(self) -> list[str]:
        """
        Build command-line arguments for the Rust binary.

        Returns:
            List of command-line arguments
        """
        # Get the absolute path to the binary
        binary_path = Path(self.config.rust_binary)
        if not binary_path.is_absolute():
            binary_path = Path.cwd() / binary_path

        args = [
            str(binary_path),
            "--fps", str(self.config.fps),
            "--width", str(self.config.width),
            "--height", str(self.config.height),
            "--exposure", str(self.config.exposure_time),
            "--offset-x", str(self.config.offset_x),
            "--offset-y", str(self.config.offset_y),
            "--serial", str(self.config.serial),
            "--t-before", str(self.config.pre_trigger_time),
            "--t-after", str(self.config.post_trigger_time),
            "--address", self.config.zmq_address,
            "--sub-port", self.config.zmq_port,
            "--save-folder", self.config.save_folder,
        ]

        return args

    def initialize(self) -> bool:
        """
        Initialize the camera process with pre-flight checks.

        Returns:
            True if initialization was successful, False otherwise
        """
        try:
            # Run pre-flight checks
            self.logger.info("Running pre-flight checks...")
            check_results = check_camera_prerequisites(self.config_path)

            # Log warnings
            for warning in check_results["warnings"]:
                self.logger.warning(warning)

            # Log errors
            for error in check_results["errors"]:
                self.logger.error(error)

            # Check overall result
            if not check_results["overall"]:
                self.logger.error("Pre-flight checks failed")
                return False

            self.logger.info("All pre-flight checks passed")
            self.is_initialized = True
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize CameraProcess: {e}")
            return False

    def _start_camera_subprocess(self) -> bool:
        """
        Start the Rust camera binary as a subprocess.

        Returns:
            True if subprocess started successfully, False otherwise
        """
        try:
            args = self._build_command_args()
            self.logger.info(f"Starting camera subprocess: {' '.join(args)}")

            # Start the subprocess with stdout/stderr piped for logging
            self.camera_subprocess = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Give it a moment to start
            time.sleep(0.5)

            # Check if it's still running
            if self.camera_subprocess.poll() is not None:
                self.logger.error(
                    f"Camera subprocess exited immediately with code "
                    f"{self.camera_subprocess.returncode}"
                )
                return False

            self.logger.info(
                f"Camera subprocess started (PID: {self.camera_subprocess.pid})"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to start camera subprocess: {e}")
            return False

    def _monitor_subprocess_output(self) -> None:
        """Monitor and log output from the camera subprocess."""
        import select

        if self.camera_subprocess and self.camera_subprocess.stdout:
            try:
                # Check if data is available before reading (avoid blocking)
                ready, _, _ = select.select(
                    [self.camera_subprocess.stdout], [], [], 0.01
                )
                if ready:
                    line = self.camera_subprocess.stdout.readline()
                    if line:
                        self.logger.info(f"[Camera] {line.strip()}")
            except Exception as e:
                self.logger.error(f"Error reading subprocess output: {e}")

    def run(self) -> None:
        """
        Main process loop for the camera manager.
        """
        if not self.is_initialized and not self.initialize():
            self.logger.error("Failed to initialize, exiting process")
            return

        self.logger.info("Starting CameraProcess")

        # Start the camera subprocess
        if not self._start_camera_subprocess():
            self.logger.error("Failed to start camera subprocess")
            return

        # Monitor subprocess
        while not self.stop_event.is_set():
            # Check if subprocess is still running
            if self.camera_subprocess.poll() is not None:
                self.logger.error(
                    f"Camera subprocess exited with code "
                    f"{self.camera_subprocess.returncode}"
                )
                break

            # Monitor output
            self._monitor_subprocess_output()

            # Small sleep to avoid busy waiting
            time.sleep(0.01)

        # Clean up
        self.logger.info("Stopping CameraProcess")
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources and terminate subprocess."""
        if self.camera_subprocess:
            if self.camera_subprocess.poll() is None:
                # Process is still running, send kill signal via ZMQ
                # (The camera binary listens for "kill" on the ZMQ socket)
                try:
                    import zmq
                    context = zmq.Context()
                    publisher = context.socket(zmq.PUB)
                    address = f"tcp://{self.config.zmq_address}:{self.config.zmq_port}"
                    publisher.connect(address)
                    time.sleep(0.1)  # Let connection establish
                    publisher.send_string("kill")
                    self.logger.info("Sent kill signal to camera subprocess")
                    time.sleep(0.5)  # Give it time to shutdown gracefully
                    publisher.close()
                    context.term()
                except Exception as e:
                    self.logger.error(f"Error sending kill signal: {e}")

                # If still running, terminate
                if self.camera_subprocess.poll() is None:
                    self.logger.warning("Terminating camera subprocess")
                    self.camera_subprocess.terminate()
                    try:
                        self.camera_subprocess.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.logger.error("Force killing camera subprocess")
                        self.camera_subprocess.kill()

            self.logger.info("Camera subprocess stopped")

        self.logger.info("CameraProcess cleaned up successfully")


# Example usage when run directly
if __name__ == "__main__":
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Camera Process")
    parser.add_argument(
        "--config", "-c", default="configs/config.toml", help="Path to config file"
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level",
    )
    args = parser.parse_args()

    # Create and run camera process
    stop_event = mp.Event()
    camera = CameraProcess(
        config_path=args.config, event=stop_event, log_level=args.log_level
    )

    try:
        if camera.initialize():
            camera.start()
            print("Camera process started. Press Ctrl+C to stop")

            # Wait for process to complete
            camera.join()
        else:
            print("Failed to initialize camera")
    except KeyboardInterrupt:
        print("\nInterrupted, stopping camera...")
        stop_event.set()
        camera.join(timeout=5)
