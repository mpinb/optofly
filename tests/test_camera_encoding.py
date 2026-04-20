"""Integration tests for camera ffmpeg encoding pipeline.

Generates synthetic grayscale frames and pipes them through the real
_build_ffmpeg_cmd / encoder_loop path, verifying the output is a valid video.

Requires: ffmpeg on PATH. NVENC tests are skipped if no GPU encoder is available.
"""

import os
import queue
import subprocess
import threading

import numpy as np
import pytest

from src.processes.camera import _build_ffmpeg_cmd, _detect_nvenc, encoder_loop

# Test dimensions — smaller than production (2112x2112) to keep tests fast
WIDTH = 512
HEIGHT = 512
FPS = 30
N_FRAMES = 90  # 3 seconds at 30fps


def _make_synthetic_buffer(width, height, n_frames):
    """Create a buffer of synthetic grayscale frames with varying content.

    Generates frames with a moving gradient + noise so the encoder
    exercises real spatial/temporal prediction (not just solid black).
    """
    buf = np.empty((n_frames, height, width), dtype=np.uint8)
    for i in range(n_frames):
        # Horizontal gradient that shifts each frame
        col = np.arange(width, dtype=np.float32)
        grad = ((col + i * 3) % 256).astype(np.uint8)
        frame = np.tile(grad, (height, 1))
        # Add some noise for spatial complexity
        noise = np.random.randint(0, 20, (height, width), dtype=np.uint8)
        buf[i] = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return buf


def _make_metadata(n_frames):
    """Create synthetic metadata array matching encoder_loop expectations."""
    # Columns: nframe, ts_sec, ts_usec, cam_time_ns
    meta = np.zeros((n_frames, 4), dtype=np.int64)
    for i in range(n_frames):
        meta[i] = [i, 1000 + i, i * 1000, i * 1_000_000]
    return meta


def _probe_video(path):
    """Use ffprobe to get video stream info. Returns dict or None."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,nb_frames,codec_name,pix_fmt",
                "-of",
                "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split(",")
        if len(parts) >= 4:
            return {
                "codec": parts[0],
                "width": int(parts[1]),
                "height": int(parts[2]),
                "pix_fmt": parts[3],
                "nb_frames": parts[4] if len(parts) > 4 else "N/A",
            }
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Direct ffmpeg pipe tests (bypass encoder_loop, test _build_ffmpeg_cmd)
# ---------------------------------------------------------------------------


class TestBuildFfmpegCmd:
    """Verify the ffmpeg command structure is correct."""

    def test_nvenc_cmd_structure(self):
        cmd = _build_ffmpeg_cmd("/tmp/test.mp4", 2112, 2112, 500, use_nvenc=True)
        assert "h264_nvenc" in cmd
        assert "-tune" not in cmd, "ULL tune should have been removed"
        assert "-delay" not in cmd, "delay flag should have been removed"
        assert "-level" not in cmd, "level should be auto (omitted)"
        # Verify new settings are present
        idx = cmd.index("-rc-lookahead")
        assert cmd[idx + 1] == "32"
        idx = cmd.index("-spatial-aq")
        assert cmd[idx + 1] == "1"
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "p4"

    def test_x264_cmd_structure(self):
        cmd = _build_ffmpeg_cmd("/tmp/test.mp4", 2112, 2112, 500, use_nvenc=False)
        assert "libx264" in cmd
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "ultrafast"
        idx = cmd.index("-crf")
        assert cmd[idx + 1] == "18"


class TestFfmpegPipeDirect:
    """Test piping raw frames to ffmpeg using the exact commands we generate."""

    def test_x264_encode(self, tmp_path):
        """x264 fallback should always work (no GPU needed)."""
        video_path = str(tmp_path / "test_x264.mp4")
        buf = _make_synthetic_buffer(WIDTH, HEIGHT, N_FRAMES)

        cmd = _build_ffmpeg_cmd(video_path, WIDTH, HEIGHT, FPS, use_nvenc=False)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = proc.communicate(input=memoryview(buf))

        assert proc.returncode == 0, f"ffmpeg x264 failed: {stderr.decode()}"
        assert os.path.exists(video_path)
        assert os.path.getsize(video_path) > 0

        info = _probe_video(video_path)
        assert info is not None, "ffprobe couldn't read the video"
        assert info["codec"] == "h264"
        assert info["width"] == WIDTH
        assert info["height"] == HEIGHT

    @pytest.mark.skipif(not _detect_nvenc(), reason="h264_nvenc not available")
    def test_nvenc_encode(self, tmp_path):
        """NVENC encode with our production settings."""
        video_path = str(tmp_path / "test_nvenc.mp4")
        buf = _make_synthetic_buffer(WIDTH, HEIGHT, N_FRAMES)

        cmd = _build_ffmpeg_cmd(video_path, WIDTH, HEIGHT, FPS, use_nvenc=True)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = proc.communicate(input=memoryview(buf))

        assert proc.returncode == 0, f"ffmpeg nvenc failed: {stderr.decode()}"
        assert os.path.exists(video_path)
        assert os.path.getsize(video_path) > 0

        info = _probe_video(video_path)
        assert info is not None, "ffprobe couldn't read the video"
        assert info["codec"] == "h264"
        assert info["width"] == WIDTH
        assert info["height"] == HEIGHT

    @pytest.mark.skipif(not _detect_nvenc(), reason="h264_nvenc not available")
    def test_nvenc_full_resolution(self, tmp_path):
        """NVENC at production resolution (2112x2112), fewer frames."""
        video_path = str(tmp_path / "test_nvenc_fullres.mp4")
        n_frames = 10  # Just enough to verify settings work at full res
        buf = np.random.randint(0, 256, (n_frames, 2112, 2112), dtype=np.uint8)

        cmd = _build_ffmpeg_cmd(video_path, 2112, 2112, 500, use_nvenc=True)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = proc.communicate(input=memoryview(buf))

        assert proc.returncode == 0, f"ffmpeg nvenc full-res failed: {stderr.decode()}"
        info = _probe_video(video_path)
        assert info is not None
        assert info["width"] == 2112
        assert info["height"] == 2112


# ---------------------------------------------------------------------------
# encoder_loop integration test (full pipeline including CSV + histograms)
# ---------------------------------------------------------------------------


class TestEncoderLoop:
    """Test the full encoder_loop thread with synthetic data."""

    def test_encoder_loop_produces_video_and_csv(self, tmp_path):
        """Feed a buffer through encoder_loop and verify all outputs."""
        buf = _make_synthetic_buffer(WIDTH, HEIGHT, N_FRAMES)
        metadata = _make_metadata(N_FRAMES)
        base_name = str(tmp_path / "obj_id_42_frame_100")

        q = queue.Queue()
        done = threading.Event()

        # Start encoder thread
        t = threading.Thread(
            target=encoder_loop,
            args=(q, FPS, WIDTH, HEIGHT, done),
        )
        t.start()

        # Enqueue one recording (trigger_frame_idx=42 simulates a pre-triggered trial)
        q.put((buf, metadata, N_FRAMES, base_name, 42))

        # Wait for encoding to finish
        q.join()
        done.set()
        t.join(timeout=10)
        assert not t.is_alive(), "encoder thread didn't exit"

        # Verify video
        video_path = f"{base_name}.mp4"
        assert os.path.exists(video_path), "video file not created"
        assert os.path.getsize(video_path) > 0, "video file is empty"
        info = _probe_video(video_path)
        assert info is not None, "ffprobe couldn't read the video"
        assert info["codec"] == "h264"

        # Verify CSV
        csv_path = f"{base_name}.csv"
        assert os.path.exists(csv_path), "CSV metadata not created"
        with open(csv_path) as f:
            lines = f.readlines()
        assert lines[0].strip() == "frame_idx,nframe,ts_sec,ts_usec,cam_time_ns,trigger_frame_idx"
        assert len(lines) == N_FRAMES + 1  # header + data rows

        # Verify debug histogram
        debug_path = f"{base_name}_debug.png"
        assert os.path.exists(debug_path), "debug histogram not created"
