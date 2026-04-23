"""
Ximea Camera Process module for triggered video recording.

Uses ximea-py for in-process capture with a circular double-buffer
and a background encoder thread (ffmpeg pipe with NVENC/x264 fallback).
"""

import csv
import ctypes
import json
import logging
import multiprocessing as mp
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import zmq

from src.utils.config import CameraConfig, ZMQConfig
from src.utils.worker import WorkerProcess

log = logging.getLogger(__name__)

# State machine constants
IDLE = 0
RECORDING = 1

# Rolling FPS reporting window
FPS_WINDOW = 500

METADATA_COLS = ["nframe", "ts_sec", "ts_usec", "cam_time_ns"]


# ---------------------------------------------------------------------------
# Debug histogram helper (from ximea-py triggered_capture.py)
# ---------------------------------------------------------------------------


def _annotate_hist(ax: plt.Axes, diffs: np.ndarray) -> None:
    """Add median line and stats box to a histogram axis."""
    ax.axvline(np.median(diffs), color="red", linestyle="--", label="median")
    stats_text = (
        f"mean={np.mean(diffs):.1f}\n"
        f"std={np.std(diffs):.1f}\n"
        f"min={np.min(diffs)}\n"
        f"max={np.max(diffs)}"
    )
    ax.text(
        0.97,
        0.95,
        stats_text,
        transform=ax.transAxes,
        verticalalignment="top",
        horizontalalignment="right",
        fontsize=8,
        fontfamily="monospace",
        bbox={"boxstyle": "round", "facecolor": "wheat", "alpha": 0.8},
    )
    ax.legend(fontsize=8)


def save_debug_histograms(metadata: np.ndarray, n_frames: int, base_path: str) -> None:
    """Save debug histograms: nframe diffs, inter-frame time, jitter, and timeline."""
    if n_frames < 2:
        log.warning("Skipping debug histograms: need ≥2 frames, got %d", n_frames)
        return
    meta = metadata[:n_frames]
    cam_time_us = meta[:, 1] * 1_000_000 + meta[:, 2]
    ifi_us = np.diff(cam_time_us).astype(np.float64)
    nframe_diffs = np.diff(meta[:, 0])

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle("Capture debug diagnostics", fontsize=14)

    ax = axes[0, 0]
    ax.hist(nframe_diffs, bins="auto", edgecolor="black", linewidth=0.5)
    ax.set_title("Frame counter diff (expect all 1)")
    ax.set_xlabel("nframe[i+1] - nframe[i]")
    ax.set_ylabel("count")
    _annotate_hist(ax, nframe_diffs)

    ax = axes[0, 1]
    ax.hist(ifi_us, bins="auto", edgecolor="black", linewidth=0.5)
    ax.set_title("Inter-frame interval (us)")
    ax.set_xlabel("us")
    ax.set_ylabel("count")
    _annotate_hist(ax, ifi_us)

    ax = axes[1, 0]
    median_ifi = np.median(ifi_us)
    jitter_us = ifi_us - median_ifi
    ax.hist(jitter_us, bins="auto", edgecolor="black", linewidth=0.5)
    ax.set_title(f"Jitter (deviation from {median_ifi:.0f} us median)")
    ax.set_xlabel("us")
    ax.set_ylabel("count")
    _annotate_hist(ax, jitter_us)

    ax = axes[1, 1]
    ax.plot(ifi_us, linewidth=0.5, alpha=0.7)
    ax.axhline(median_ifi, color="red", linestyle="--", linewidth=1, label="median")
    ax.set_title("Inter-frame interval over time")
    ax.set_xlabel("frame index")
    ax.set_ylabel("us")
    ax.legend(fontsize=8)

    fig.tight_layout()
    png_path = f"{base_path}_debug.png"
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    log.info("debug histograms: %s", png_path)


# ---------------------------------------------------------------------------
# Encoder thread
# ---------------------------------------------------------------------------


def _detect_nvenc() -> bool:
    """Check once whether h264_nvenc is available via ffmpeg."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False


def _build_ffmpeg_cmd(
    video_path: str,
    width: int,
    height: int,
    fps: int,
    use_nvenc: bool,
) -> list[str]:
    """Build the ffmpeg command for encoding grayscale frames from stdin."""
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "warning",
        "-thread_queue_size",
        "512",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
    ]
    if use_nvenc:
        cmd += [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p4",
            "-bf",
            "0",
            "-rc",
            "constqp",
            "-qp",
            "18",
            "-rc-lookahead",
            "32",
            "-spatial-aq",
            "1",
            "-pix_fmt",
            "nv12",
            "-profile:v",
            "high",
        ]
    else:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
        ]
    cmd.append(video_path)
    return cmd


def encoder_loop(
    q: queue.Queue,
    fps: int,
    width: int,
    height: int,
    done_event: threading.Event,
) -> None:
    """Persistent encoder thread.

    Pulls (buf, meta, n_filled, base_name, trigger_frame_idx) from queue.
    Pipes raw frames to ffmpeg stdin (linear buffer, no ring indexing).
    """
    use_nvenc = _detect_nvenc()
    log.info("Encoder: using %s", "h264_nvenc" if use_nvenc else "libx264")

    while not done_event.is_set() or not q.empty():
        try:
            buf, metadata, n_filled, base_name, trigger_frame_idx = q.get(timeout=0.5)
        except queue.Empty:
            continue

        video_path = f"{base_name}.mp4"
        csv_path = f"{base_name}.csv"
        log.info("writing %d frames to %s", n_filled, video_path)

        t0 = time.perf_counter()

        cmd = _build_ffmpeg_cmd(video_path, width, height, fps, use_nvenc)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        # memoryview avoids duplicating the ~2GB buffer in RAM
        input_data = memoryview(buf[:n_filled])

        # communicate() drains stderr while writing stdin concurrently,
        # preventing deadlock when ffmpeg fills the stderr pipe buffer
        _, stderr_bytes = proc.communicate(input=input_data)

        if proc.returncode != 0:
            log.error(
                "ffmpeg exited %d: %s", proc.returncode, stderr_bytes.decode().strip()
            )
            if use_nvenc:
                log.warning("Retrying with libx264")
                cmd = _build_ffmpeg_cmd(video_path, width, height, fps, False)
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                _, stderr_bytes = proc.communicate(input=input_data)
                if proc.returncode != 0:
                    log.error("libx264 fallback also failed, skipping this recording")
                    q.task_done()
                    continue
                use_nvenc = False

        # Write CSV metadata (linear order)
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "frame_idx",
                    "nframe",
                    "ts_sec",
                    "ts_usec",
                    "cam_time_ns",
                    "trigger_frame_idx",
                ]
            )
            for i in range(n_filled):
                row = metadata[i]
                writer.writerow([i, row[0], row[1], row[2], row[3], trigger_frame_idx])

        save_debug_histograms(metadata, n_filled, base_name)

        elapsed = time.perf_counter() - t0
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        log.info(
            "encode done: %.1f MB, %.2fs (%d fps encode), csv: %s",
            size_mb,
            elapsed,
            int(n_filled / elapsed),
            csv_path,
        )
        q.task_done()


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


def check_camera_prerequisites(config_path: str = "configs/config.toml") -> dict:
    """
    Run pre-flight checks for the camera system.

    Returns:
        Dictionary with check results and overall pass/fail.
    """
    config = CameraConfig(config_path)
    results = {
        "binary": False,
        "ffmpeg": False,
        "save_folder": False,
        "zmq_port": False,
        "overall": False,
        "errors": [],
        "warnings": [],
    }

    # Check 1: optofly-camera Rust binary findable
    project_root = Path(__file__).parent.parent.parent
    binary_candidates = [
        project_root / "optofly-camera" / "target" / "release" / "optofly-camera",
        project_root / "optofly-camera" / "target" / "debug" / "optofly-camera",
    ]
    found_binary = any(p.exists() for p in binary_candidates) or shutil.which(
        "optofly-camera"
    )
    if found_binary:
        results["binary"] = True
    else:
        results["errors"].append("optofly-camera binary not found")
        results["errors"].append("Build: cd optofly-camera && cargo build --release")

    # Check 2: ffmpeg available
    if shutil.which("ffmpeg"):
        results["ffmpeg"] = True
        if _detect_nvenc():
            log.info("h264_nvenc available")
        else:
            results["warnings"].append(
                "h264_nvenc not available — will fall back to libx264 (slower)"
            )
    else:
        results["errors"].append("ffmpeg not found in PATH")
        results["errors"].append("Install: sudo apt-get install ffmpeg")

    # Check 3: Save folder is writable
    save_path = Path(config.save_folder)
    if not save_path.is_absolute():
        config_dir = Path(config_path).parent
        save_path = (config_dir / save_path).resolve()

    # Check that the parent exists and is writable without creating the folder itself.
    # The actual save folder is determined per-session at runtime.
    check_path = save_path if save_path.exists() else save_path.parent
    try:
        test_file = check_path / ".write_test"
        test_file.touch()
        test_file.unlink()
        results["save_folder"] = True
    except Exception as e:
        results["errors"].append(f"Save folder parent not writable: {check_path}")
        results["errors"].append(f"Error: {e}")

    # Check 4: ZMQ port reachable
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((config.zmq_address, int(config.zmq_port)))
        sock.close()
        if result == 0:
            results["warnings"].append(f"ZMQ port {config.zmq_port} is already in use")
            results["warnings"].append("The camera will connect to existing publisher")
        results["zmq_port"] = True
    except Exception as e:
        results["warnings"].append(f"Could not check ZMQ port: {e}")
        results["zmq_port"] = True

    results["overall"] = (
        results["binary"]
        and results["ffmpeg"]
        and results["save_folder"]
        and results["zmq_port"]
    )
    return results


# ---------------------------------------------------------------------------
# Camera process
# ---------------------------------------------------------------------------


class CameraProcess(WorkerProcess):
    """
    Process for triggered high-speed video capture using ximea-py.

    Replaces the previous Rust subprocess approach with in-process
    capture, circular double-buffers, and a background encoder thread.
    """

    def __init__(
        self,
        config_path: str = "configs/config.toml",
        event: Optional[mp.Event] = None,
        save_folder: Optional[str] = None,
        process_name: str = "CameraProcess",
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

        self.config = CameraConfig(config_path)
        if save_folder is not None:
            self.config.save_folder = save_folder
        self.config_path = config_path
        self.stop_event = event if event is not None else mp.Event()

        # ZMQ config loaded later in run() to avoid fork issues
        self.zmq_config_path = config_path

    def _run(self) -> None:
        """Main capture loop — runs inside the child process."""
        from ximea import Camera, Image

        self.logger.info("Starting CameraProcess")

        # Pre-flight checks
        check_results = check_camera_prerequisites(self.config_path)
        for w in check_results["warnings"]:
            self.logger.warning(w)
        for e in check_results["errors"]:
            self.logger.error(e)
        if not check_results["overall"]:
            self.logger.error("Pre-flight checks failed, exiting")
            return

        # Load ZMQ config in child process
        zmq_config = ZMQConfig(self.zmq_config_path)

        # Create save folder
        save_path = Path(self.config.save_folder)
        save_path.mkdir(parents=True, exist_ok=True)
        self.logger.info("Save folder: %s", save_path)

        # --- ZMQ subscriber ---
        zmq_ctx = zmq.Context()
        zmq_sub = zmq_ctx.socket(zmq.SUB)
        zmq_sub.connect(zmq_config.get_subscriber_address(zmq_config.trigger_port))
        zmq_sub.setsockopt_string(zmq.SUBSCRIBE, zmq_config.zone_enter_topic)
        zmq_sub.setsockopt_string(zmq.SUBSCRIBE, zmq_config.zone_exit_topic)
        zmq_sub.setsockopt_string(zmq.SUBSCRIBE, zmq_config.pre_zone_enter_topic)
        zmq_sub.setsockopt_string(zmq.SUBSCRIBE, zmq_config.pre_zone_exit_topic)
        zmq_sub.setsockopt_string(zmq.SUBSCRIBE, "kill")
        self.logger.info(
            "ZMQ SUB connected to trigger port %d (topics: %s, %s, %s, %s, kill)",
            zmq_config.trigger_port,
            zmq_config.zone_enter_topic,
            zmq_config.zone_exit_topic,
            zmq_config.pre_zone_enter_topic,
            zmq_config.pre_zone_exit_topic,
        )

        # --- Camera setup ---
        cam = Camera()
        cam.open_device()
        cam.set_imgdataformat("XI_MONO8")
        cam.set_exposure(int(self.config.exposure_time))

        # Sensor corrections
        cam.enable_bpc()
        cam.set_column_fpn_correction("XI_ON")

        # ROI — set dimensions first, then center on sensor
        cam.set_width(self.config.width)
        cam.set_height(self.config.height)

        # Auto-center: compute offset to place ROI in the middle of the sensor
        sensor_w = cam.get_width_maximum()
        sensor_h = cam.get_height_maximum()
        offset_x = (sensor_w - self.config.width) // 2
        offset_y = (sensor_h - self.config.height) // 2

        # Snap to increment (Ximea sensors require aligned offsets)
        inc_x = cam.get_offsetX_increment()
        inc_y = cam.get_offsetY_increment()
        offset_x = (offset_x // inc_x) * inc_x
        offset_y = (offset_y // inc_y) * inc_y

        cam.set_offsetX(offset_x)
        cam.set_offsetY(offset_y)
        self.logger.info(
            "Sensor %dx%d, ROI %dx%d, offset (%d, %d)",
            sensor_w,
            sensor_h,
            self.config.width,
            self.config.height,
            offset_x,
            offset_y,
        )

        # Frame rate
        cam.set_acq_timing_mode("XI_ACQ_TIMING_MODE_FRAME_RATE_LIMIT")
        cam.set_framerate(self.config.fps)

        width = cam.get_width()
        height = cam.get_height()
        frame_bytes = width * height
        fps = int(self.config.fps)

        self.logger.info("Resolution: %dx%d @ %d fps", width, height, fps)

        # --- Allocate double buffers (linear, no ring) ---
        # Buffer sized from max_recording_time (+1s margin for message latency)
        max_recording_time = self.config.max_recording_time + 1.0
        buf_size = int(fps * max_recording_time)

        self.logger.info(
            "Buffer: %d frames (%.1f s from max_recording_time=%.1f, %.1f MB x2)",
            buf_size,
            max_recording_time,
            self.config.max_recording_time,
            buf_size * frame_bytes / (1024**2),
        )

        buffers = [
            np.empty((buf_size, height, width), dtype=np.uint8),
            np.empty((buf_size, height, width), dtype=np.uint8),
        ]
        meta_buffers = [
            np.zeros((buf_size, 4), dtype=np.int64),
            np.zeros((buf_size, 4), dtype=np.int64),
        ]
        active_idx = 0

        # --- Encoder thread ---
        encode_queue: queue.Queue = queue.Queue(maxsize=2)
        done_event = threading.Event()
        encoder = threading.Thread(
            target=encoder_loop,
            args=(encode_queue, fps, width, height, done_event),
            daemon=True,
        )
        encoder.start()

        # --- Capture state ---
        img = Image()
        state = IDLE
        buf_idx = 0  # current write index in active buffer (reset on each recording)
        recording_obj_id = None
        recording_frame = None
        trigger_frame_idx: Optional[int] = None  # buf_idx when real ZONE_ENTER fires
        rec_dropped = 0
        rec_prev_nframe = None
        total_frames = 0
        last_status_time = time.perf_counter()
        last_status_frames = 0

        # Cache for hot loop
        _memmove = ctypes.memmove
        _debug = self.logger.isEnabledFor(logging.DEBUG)

        def _finish_recording(reason: str = "unknown"):
            """Flush current buffer to encoder and swap to standby."""
            nonlocal \
                active_idx, \
                buf_idx, \
                state, \
                recording_obj_id, \
                recording_frame, \
                rec_dropped, \
                trigger_frame_idx
            n_filled = buf_idx
            if n_filled == 0:
                self.logger.warning("Recording ended with 0 frames, skipping encode")
                state = IDLE
                recording_obj_id = None
                recording_frame = None
                return

            base_name = str(
                save_path / f"obj_id_{recording_obj_id}_frame_{recording_frame}"
            )
            if encode_queue.full():
                self.logger.warning("Encoder busy, skipping this recording")
            else:
                encode_queue.put(
                    (
                        buffers[active_idx],
                        meta_buffers[active_idx],
                        n_filled,
                        base_name,
                        trigger_frame_idx,
                    )
                )

            # Swap to standby buffer
            active_idx = 1 - active_idx
            buf_idx = 0
            state = IDLE
            self.logger.info(
                "Recording done: %d frames, %d dropped, reason=%s, back to IDLE",
                n_filled,
                rec_dropped,
                reason,
            )
            recording_obj_id = None
            recording_frame = None
            trigger_frame_idx = None

        try:
            cam.start_acquisition()
            self.logger.info("Acquisition started — entering capture loop")

            while not self.stop_event.is_set():
                cam.get_image(img, timeout=5000)
                total_frames += 1

                # --- State machine ---
                if state == IDLE:
                    # Not recording — poll ZMQ for PRE_ZONE_ENTER or ZONE_ENTER
                    # (every frame is fine, no buffer write overhead while idle)
                    try:
                        topic, message = zmq_sub.recv_multipart(flags=zmq.NOBLOCK)
                        topic_str = topic.decode()
                        if topic_str == "kill":
                            self.logger.info("Received kill signal")
                            break
                        elif topic_str == zmq_config.pre_zone_enter_topic:
                            # PRE_ZONE_ENTER: start recording early; real ZONE_ENTER not yet seen
                            msg = json.loads(message.decode())
                            recording_obj_id = msg["obj_id"]
                            recording_frame = msg.get("frame", 0)
                            buf_idx = 0
                            rec_dropped = 0
                            rec_prev_nframe = None
                            trigger_frame_idx = None  # will be set on real ZONE_ENTER
                            state = RECORDING
                            self.logger.info(
                                "PRE_ZONE_ENTER obj_id=%s — started recording early (max %d frames)",
                                recording_obj_id,
                                buf_size,
                            )
                        elif topic_str == zmq_config.zone_enter_topic:
                            # ZONE_ENTER in IDLE: backward compat (pre_zone_expansion=0)
                            msg = json.loads(message.decode())
                            recording_obj_id = msg["obj_id"]
                            recording_frame = msg.get("frame", 0)
                            buf_idx = 0
                            rec_dropped = 0
                            rec_prev_nframe = None
                            trigger_frame_idx = 0  # recording started at real trigger
                            state = RECORDING
                            self.logger.info(
                                "ZONE_ENTER obj_id=%s — started recording (max %d frames)",
                                recording_obj_id,
                                buf_size,
                            )
                    except zmq.Again:
                        pass

                elif state == RECORDING:
                    # Per-video dropped frame tracking
                    if rec_prev_nframe is not None:
                        gap = img.nframe - rec_prev_nframe - 1
                        if gap > 0:
                            rec_dropped += gap
                    rec_prev_nframe = img.nframe

                    # Write frame into linear buffer
                    _memmove(
                        buffers[active_idx][buf_idx].ctypes.data, img.bp, frame_bytes
                    )
                    cam_time_ns = (
                        int(img.tsSec) * 1_000_000_000 + int(img.tsUSec) * 1_000
                    )
                    meta_buffers[active_idx][buf_idx] = (
                        img.nframe,
                        img.tsSec,
                        img.tsUSec,
                        cam_time_ns,
                    )
                    buf_idx += 1

                    # Poll ZMQ every frame — need to catch ZONE_EXIT / PRE_ZONE_EXIT promptly
                    try:
                        topic, message = zmq_sub.recv_multipart(flags=zmq.NOBLOCK)
                        topic_str = topic.decode()
                        if topic_str == "kill":
                            _finish_recording("kill")
                            self.logger.info("Received kill signal during recording")
                            break
                        elif topic_str == zmq_config.zone_enter_topic:
                            # Real ZONE_ENTER while already recording (started by PRE_ZONE_ENTER)
                            msg = json.loads(message.decode())
                            if msg["obj_id"] == recording_obj_id:
                                trigger_frame_idx = buf_idx
                                self.logger.info(
                                    "ZONE_ENTER obj_id=%s at buf_idx=%d (pre-trigger frames=%d)",
                                    recording_obj_id,
                                    trigger_frame_idx,
                                    trigger_frame_idx,
                                )
                        elif topic_str == zmq_config.zone_exit_topic:
                            msg = json.loads(message.decode())
                            if msg["obj_id"] == recording_obj_id:
                                exit_reason = msg.get("reason", "unknown")
                                _finish_recording(exit_reason)
                        elif topic_str == zmq_config.pre_zone_exit_topic:
                            msg = json.loads(message.decode())
                            if (
                                msg["obj_id"] == recording_obj_id
                                and trigger_frame_idx is None
                            ):
                                # Fly left pre-zone without ever reaching real zone — stop recording
                                exit_reason = msg.get("reason", "left_pre_zone")
                                self.logger.info(
                                    "PRE_ZONE_EXIT obj_id=%s before real ZONE_ENTER — stopping recording",
                                    recording_obj_id,
                                )
                                _finish_recording(exit_reason)
                    except zmq.Again:
                        pass

                    # Safety: buffer full (fly stayed > max_recording_time)
                    if state == RECORDING and buf_idx >= buf_size:
                        self.logger.warning(
                            "Buffer full (%d frames), forcing recording stop",
                            buf_size,
                        )
                        _finish_recording("buffer_full")

                # Periodic FPS reporting (only when debug logging is active)
                if _debug and total_frames % FPS_WINDOW == 0:
                    now = time.perf_counter()
                    dt = now - last_status_time
                    if dt > 0:
                        rolling_fps = (total_frames - last_status_frames) / dt
                        self.logger.debug(
                            "[%s] frames=%d fps=%.1f",
                            "IDLE"
                            if state == IDLE
                            else f"RECORDING ({buf_idx}/{buf_size})",
                            total_frames,
                            rolling_fps,
                        )
                    last_status_time = now
                    last_status_frames = total_frames

        except Exception as e:
            self.logger.error("Capture loop error: %s", e, exc_info=True)
        finally:
            # If we were recording, flush the buffer
            if state == RECORDING and buf_idx > 0:
                _finish_recording("shutdown")

            cam.stop_acquisition()
            cam.close_device()
            self.logger.info("Camera stopped. Total frames: %d", total_frames)

            # Wait for encoder to finish
            done_event.set()
            encoder.join(timeout=30)

            # Cleanup ZMQ
            zmq_sub.close()
            zmq_ctx.term()
            self.logger.info("CameraProcess cleaned up successfully")


class RustCameraProcess(WorkerProcess):
    """
    Camera process that launches the Rust optofly-camera binary as a subprocess.

    Drop-in replacement for CameraProcess — same interface for main.py.
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
        self.save_folder = save_folder or CameraConfig(config_path).save_folder
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
        except FileNotFoundError as e:
            self.logger.error(str(e))
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

        # Start a thread to forward Rust binary output to logger
        output_thread = threading.Thread(
            target=self._forward_output,
            daemon=True,
        )
        output_thread.start()

        # Wait for either stop_event or process exit
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

        # Stop event set — send SIGTERM for graceful shutdown
        # Allow enough time for the encoder to finish any in-progress recording
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


# Allow running as standalone module for testing
if __name__ == "__main__":
    import argparse

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

    stop_event = mp.Event()
    camera = CameraProcess(
        config_path=args.config, event=stop_event, log_level=args.log_level
    )

    try:
        camera.start()
        print("Camera process started. Press Ctrl+C to stop")
        camera.join()
    except KeyboardInterrupt:
        print("\nInterrupted, stopping camera...")
        stop_event.set()
        camera.join(timeout=5)
