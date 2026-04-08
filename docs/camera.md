# Ximea Camera

High-speed triggered video recording using ximea-py and ffmpeg.

**Specifications:**
- 500fps at 2112x2112 pixels
- H.264 encoding with NVENC hardware acceleration (x264 fallback)
- Double-buffer design for zero-copy, race-free operation
- ~8GB memory footprint (default settings, 2 buffers)

## Architecture

Single-process design with a background encoder thread:

1. **Capture loop** (in `CameraProcess.run()`) — captures frames at 500fps via ximea-py, writes into a linear double-buffer using `ctypes.memmove`
2. **State machine** — IDLE polls ZMQ for ZONE_ENTER; on enter, transitions to RECORDING. In RECORDING, captures frames linearly and polls for ZONE_EXIT. On exit (or buffer full), hands buffer to encoder
3. **Encoder thread** — pipes raw frames to ffmpeg stdin (single contiguous write), writes CSV metadata and debug histograms

**Double-buffer pattern:** Two pre-allocated numpy arrays sized for `zone_timeout + 1s`. On recording completion, the active buffer is enqueued for encoding and the standby buffer becomes active. The encoder reads the old buffer while capture continues into the new one.

**Memory:** `2 x (zone_timeout + 1s) x fps x width x height`
Default settings (3s x 500fps x 2016x2016): ~5.7GB.

## Dependencies

- **ximea-py** — Python wrapper for XIMEA SDK (`uv sync` installs from git)
- **ffmpeg** — must be on PATH; NVENC requires NVIDIA drivers
- **XIMEA SDK** — system install required for camera access

## Usage

**Via main.py (normal use):**
Set `[camera] active = true` in `configs/config.toml`, then `uv run python main.py`.

**Standalone:**
```bash
uv run python -m src.processes.camera --config configs/config.toml --log-level DEBUG
```

**Pre-flight checks:**
```python
from src.processes.camera import check_camera_prerequisites
results = check_camera_prerequisites("configs/config.toml")
if not results["overall"]:
    for error in results["errors"]:
        print(error)
```

## ZMQ Protocol

Subscribes to topics `ZONE_ENTER` and `ZONE_EXIT` on port 5556 (multipart):
```
[b"ZONE_ENTER", b'{"obj_id": 123, "frame": 4567, "x": 0.01, "y": -0.02, "z": 0.18, "mean_heading": 0.52}']
[b"ZONE_EXIT", b'{"obj_id": 123, "reason": "left_fov", "timestamp": 1234.78, "duration": 0.22}']
```

Kill signal: `[b"kill", b""]`

## Output

**Video:** `{save_folder}/obj_id_{obj_id}_frame_{frame}.mp4`
- Codec: H.264 (NVENC p7/constqp16, or x264 crf16 fallback), grayscale input

**Metadata CSV:** `{save_folder}/obj_id_{obj_id}_frame_{frame}.csv`
```csv
frame_idx,nframe,ts_sec,ts_usec,cam_time_ns
0,100,1234,567890,1234567890000
```

**Debug histograms:** `{save_folder}/obj_id_{obj_id}_frame_{frame}_debug.png`
- Frame counter diffs, inter-frame interval, jitter, timeline

## Troubleshooting

**Camera not detected:**
```bash
lsusb | grep Ximea
ldconfig -p | grep libxi
```

**NVENC not available** — ffmpeg falls back to software encoding:
```bash
sudo ubuntu-drivers autoinstall
ffmpeg -encoders | grep nvenc
```

**ZMQ port in use:**
```bash
netstat -tulpn | grep 5556
```

## Testing

```bash
# Integration test (requires hardware)
python tests/test_camera_integration.py
```
