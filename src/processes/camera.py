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
TRIGGERED = 1

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
        # Input: raw gray8 frames on stdin
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
            "p7",
            "-tune",
            "hq",
            "-rc",
            "constqp",
            "-qp",
            "16",
            "-level",
            "5.2",
        ]
    else:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "16",
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

    Pulls (buf, meta, buf_size, n_filled, ring_start, base_name) from queue.
    Pipes raw frames to ffmpeg stdin in ring-buffer order (two contiguous
    writes, no reorder copy).
    """
    use_nvenc = _detect_nvenc()
    log.info("Encoder: using %s", "h264_nvenc" if use_nvenc else "libx264")

    while not done_event.is_set() or not q.empty():
        try:
            buf, metadata, buf_size, n_filled, ring_start, base_name = q.get(
                timeout=0.5
            )
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

        # Write frames in ring-buffer order as two contiguous slices
        # — no reorder copy needed
        try:
            if n_filled == buf_size and ring_start > 0:
                proc.stdin.write(buf[ring_start:].tobytes())
                proc.stdin.write(buf[:ring_start].tobytes())
            else:
                proc.stdin.write(buf[:n_filled].tobytes())
            proc.stdin.close()
        except BrokenPipeError:
            log.error("ffmpeg pipe broke during write")

        stderr = proc.stderr.read()
        proc.wait()

        if proc.returncode != 0:
            log.error("ffmpeg exited %d: %s", proc.returncode, stderr.decode().strip())
            # Try fallback if NVENC failed
            if use_nvenc:
                log.warning("Retrying with libx264")
                cmd = _build_ffmpeg_cmd(video_path, width, height, fps, False)
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                try:
                    if n_filled == buf_size and ring_start > 0:
                        proc.stdin.write(buf[ring_start:].tobytes())
                        proc.stdin.write(buf[:ring_start].tobytes())
                    else:
                        proc.stdin.write(buf[:n_filled].tobytes())
                    proc.stdin.close()
                except BrokenPipeError:
                    log.error("ffmpeg fallback pipe broke during write")
                proc.wait()
                if proc.returncode != 0:
                    log.error("libx264 fallback also failed, skipping this trigger")
                    q.task_done()
                    continue
                # Disable NVENC for remaining encodes
                use_nvenc = False

        # Write CSV metadata in ring-buffer order
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["frame_idx", "nframe", "ts_sec", "ts_usec", "cam_time_ns"])
            for i in range(n_filled):
                idx = (ring_start + i) % buf_size
                row = metadata[idx]
                writer.writerow([i, row[0], row[1], row[2], row[3]])

        # Debug histograms — build sequential metadata here
        ordered_meta = np.empty((n_filled, 4), dtype=np.int64)
        for i in range(n_filled):
            ordered_meta[i] = metadata[(ring_start + i) % buf_size]
        save_debug_histograms(ordered_meta, n_filled, base_name)

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
        "ximea": False,
        "ffmpeg": False,
        "save_folder": False,
        "zmq_port": False,
        "overall": False,
        "errors": [],
        "warnings": [],
    }

    # Check 1: ximea-py importable
    try:
        from ximea import Camera  # noqa: F401

        results["ximea"] = True
    except ImportError as e:
        results["errors"].append(f"ximea-py not importable: {e}")
        results["errors"].append(
            "Install: uv add 'ximea @ git+https://github.com/elhananby/ximea-py.git'"
        )

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

    try:
        save_path.mkdir(parents=True, exist_ok=True)
        test_file = save_path / ".write_test"
        test_file.touch()
        test_file.unlink()
        results["save_folder"] = True
    except Exception as e:
        results["errors"].append(f"Save folder not writable: {save_path}")
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
        results["ximea"]
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
    ):
        super().__init__(
            event=event,
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

    def run(self) -> None:
        """Main capture loop — runs inside the child process."""
        from ximea import Camera, Image

        # Initialize logger in child process
        self._initialize_logger()
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
        zmq_sub.setsockopt_string(zmq.SUBSCRIBE, zmq_config.trigger_topic)
        zmq_sub.setsockopt_string(zmq.SUBSCRIBE, "kill")
        self.logger.info(
            "ZMQ SUB connected to trigger port %d", zmq_config.trigger_port
        )

        # --- Camera setup ---
        cam = Camera()
        cam.open_device()
        cam.set_imgdataformat("XI_MONO8")
        cam.set_exposure(int(self.config.exposure_time))

        # ROI
        cam.set_width(self.config.width)
        cam.set_height(self.config.height)
        cam.set_offsetX(self.config.offset_x)
        cam.set_offsetY(self.config.offset_y)

        # Frame rate
        cam.set_acq_timing_mode("XI_ACQ_TIMING_MODE_FRAME_RATE_LIMIT")
        cam.set_framerate(self.config.fps)

        width = cam.get_width()
        height = cam.get_height()
        frame_bytes = width * height
        fps = int(self.config.fps)

        self.logger.info("Resolution: %dx%d @ %d fps", width, height, fps)

        # --- Allocate double buffers ---
        n_before = int(fps * self.config.pre_trigger_time)
        n_after = int(fps * self.config.post_trigger_time)
        buf_size = n_before + n_after

        self.logger.info(
            "Buffer: %d pre + %d post = %d frames (%.1f MB x2)",
            n_before,
            n_after,
            buf_size,
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
        buf_idx = 0

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
        post_trigger_remaining = 0
        trigger_obj_id = None
        trigger_frame = None
        dropped = 0
        prev_nframe = None
        total_frames = 0
        last_status_time = time.perf_counter()
        last_status_frames = 0

        # Cache for hot loop
        _memmove = ctypes.memmove
        _debug = self.logger.isEnabledFor(logging.DEBUG)

        try:
            cam.start_acquisition()
            self.logger.info("Acquisition started — entering capture loop")

            while not self.stop_event.is_set():
                cam.get_image(img, timeout=5000)

                # Copy frame into active buffer
                slot = buf_idx % buf_size
                _memmove(buffers[active_idx][slot].ctypes.data, img.bp, frame_bytes)

                # Record metadata (camera timestamps only, no syscall)
                meta_buffers[active_idx][slot] = (
                    img.nframe,
                    img.tsSec,
                    img.tsUSec,
                    img.tsSec * 1_000_000_000 + img.tsUSec * 1000,
                )

                buf_idx += 1
                total_frames += 1

                # Dropped frame tracking
                if prev_nframe is not None:
                    gap = img.nframe - prev_nframe - 1
                    if gap > 0:
                        dropped += gap
                prev_nframe = img.nframe

                # --- State machine ---
                if state == IDLE:
                    # Poll ZMQ every 10 frames to reduce overhead
                    if total_frames % 10 == 0:
                        try:
                            topic, message = zmq_sub.recv_multipart(flags=zmq.NOBLOCK)
                            topic_str = topic.decode()
                            if topic_str == "kill":
                                self.logger.info("Received kill signal")
                                break
                            elif topic_str == zmq_config.trigger_topic:
                                msg = json.loads(message.decode())
                                trigger_obj_id = msg["obj_id"]
                                trigger_frame = msg["frame"]
                                state = TRIGGERED
                                post_trigger_remaining = n_after
                                self.logger.info(
                                    "TRIGGERED obj_id=%s frame=%s, recording %d post-trigger frames",
                                    trigger_obj_id,
                                    trigger_frame,
                                    n_after,
                                )
                        except zmq.Again:
                            pass

                elif state == TRIGGERED:
                    post_trigger_remaining -= 1
                    if post_trigger_remaining <= 0:
                        n_filled = min(buf_idx, buf_size)
                        # Oldest frame index in the ring buffer
                        ring_start = buf_idx % buf_size if n_filled == buf_size else 0

                        # Enqueue for encoding — pass the buffer directly,
                        # the encoder reads in ring order (no copy here)
                        base_name = str(
                            save_path / f"obj_id_{trigger_obj_id}_frame_{trigger_frame}"
                        )
                        if encode_queue.full():
                            self.logger.warning("Encoder busy, skipping this trigger")
                        else:
                            encode_queue.put(
                                (
                                    buffers[active_idx],
                                    meta_buffers[active_idx],
                                    buf_size,
                                    n_filled,
                                    ring_start,
                                    base_name,
                                )
                            )

                        # Swap to standby buffer
                        active_idx = 1 - active_idx
                        buf_idx = 0
                        state = IDLE
                        self.logger.info(
                            "Buffer swapped, back to IDLE (dropped so far: %d)", dropped
                        )

                # Periodic FPS reporting (only when debug logging is active)
                if _debug and total_frames % FPS_WINDOW == 0:
                    now = time.perf_counter()
                    dt = now - last_status_time
                    if dt > 0:
                        rolling_fps = (total_frames - last_status_frames) / dt
                        self.logger.debug(
                            "[%s] frames=%d fps=%.1f dropped=%d",
                            "IDLE"
                            if state == IDLE
                            else f"TRIGGERED ({post_trigger_remaining} remaining)",
                            total_frames,
                            rolling_fps,
                            dropped,
                        )
                    last_status_time = now
                    last_status_frames = total_frames

        except Exception as e:
            self.logger.error("Capture loop error: %s", e, exc_info=True)
        finally:
            cam.stop_acquisition()
            cam.close_device()
            self.logger.info(
                "Camera stopped. Total frames: %d, dropped: %d", total_frames, dropped
            )

            # Wait for encoder to finish
            done_event.set()
            encoder.join(timeout=30)

            # Cleanup ZMQ
            zmq_sub.close()
            zmq_ctx.term()
            self.logger.info("CameraProcess cleaned up successfully")


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
