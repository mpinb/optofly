# Ximea Camera

High-speed triggered video recording using the `optofly-camera` Rust binary.

**Specifications:**
- 500fps at 2112x2112 pixels (configurable)
- H.264 encoding with NVENC hardware acceleration (x264 fallback)
- Linear double-buffer design for zero-copy, race-free operation
- ~8GB memory footprint (default settings, 2 buffers)

## Architecture

The camera subsystem has two layers:

1. **`optofly-camera` (Rust binary)** — core capture and encoding logic
2. **`RustCameraProcess` (Python wrapper)** — launches the binary as a subprocess, integrates with the OptoFly process model

### Rust Binary (`optofly-camera/`)

Single-process design with a background encoder thread:

1. **Capture loop** (`capture.rs`) — opens XIMEA camera via `xiapi`, captures frames at 500fps into a linear double-buffer
2. **State machine** — `Idle` polls ZMQ for `ZONE_ENTER`; on receipt, transitions to `Recording`. In `Recording`, captures frames linearly and polls for `ZONE_EXIT`. On exit (or buffer full), hands the buffer to the encoder
3. **Encoder thread** (`encoder.rs`) — pipes raw frames to ffmpeg stdin (single contiguous write), writes CSV metadata
4. **Config** (`config.rs`) — reads `[camera]` and `[zmq]` sections from the shared TOML config

**Double-buffer pattern:** Two pre-allocated `Vec<u8>` buffers sized for `max_recording_time + 1s`. On recording completion, the active buffer is swapped via `std::mem::replace` and enqueued for encoding. The encoder reads the old buffer while capture continues into the new one.

**Memory:** `2 x (max_recording_time + 1s) x fps x width x height`
Default settings (3s, 500fps, 2112x2112): ~8.5GB.

### Python Wrapper (`src/processes/camera.py`)

`RustCameraProcess` extends `WorkerProcess` and:
- Locates the binary in `optofly-camera/target/{release,debug}/` or `PATH`
- Launches it with `--config`, `--save-folder`, and `--log-level` arguments
- Monitors the subprocess and the shared `stop_event`
- On shutdown: sends a ZMQ `kill` message, waits for graceful exit, then SIGTERM/SIGKILL

## Dependencies

- **xiapi** (Rust crate) — Rust bindings for XIMEA SDK
- **ffmpeg** — must be on PATH; NVENC requires NVIDIA drivers
- **XIMEA SDK** — system install required for camera access; install with `sudo scripts/install_ximea_driver.sh`

## Building

```bash
cd optofly-camera
cargo build --release
```

The release binary is at `optofly-camera/target/release/optofly-camera`.

## Usage

**Via main.py (normal use):**
Set `[camera] active = true` in `configs/config.toml`, then `uv run python main.py`.

**Rust binary directly:**
```bash
./optofly-camera/target/release/optofly-camera --config configs/config.toml --save-folder /tmp/videos --log-level info
```

**Pre-flight checks:**
```python
from src.processes.camera import check_camera_prerequisites
results = check_camera_prerequisites("configs/config.toml")
if not results["overall"]:
    for error in results["errors"]:
        print(error)
```

## Configuration

The Rust binary reads from the shared `configs/config.toml`:

```toml
[camera]
active = true
resolution = [2112, 2112]
fps = 500
exposure_time = 900
max_recording_time = 3.0   # seconds, controls buffer size

[zmq]
trigger_port = 5556
zone_enter_topic = "ZONE_ENTER"
zone_exit_topic = "ZONE_EXIT"
```

## ZMQ Protocol

Subscribes to topics `ZONE_ENTER`, `ZONE_EXIT`, and `kill` on port 5556 (multipart):
```
[b"ZONE_ENTER",     b'{"obj_id": 123, "frame": 4589, "x": 0.01, "y": -0.02, "z": 0.18, "mean_heading": 0.52}']
[b"ZONE_EXIT",      b'{"obj_id": 123, "reason": "left_fov", "timestamp": 1234.80, "duration": 0.20}']
```

Kill signal: `[b"kill", b""]`

**State machine:**

| State | Event | Action |
|-------|-------|--------|
| IDLE | `ZONE_ENTER` | → RECORDING; `trigger_frame_idx = 0` |
| RECORDING | `ZONE_EXIT` | finish and hand buffer to encoder |

## Output

**Video:** `{save_folder}/obj_id_{obj_id}_frame_{frame}.mp4`
- Codec: H.264 (NVENC p4/constqp18, or x264 ultrafast/crf18 fallback), grayscale input

**Metadata CSV:** `{save_folder}/obj_id_{obj_id}_frame_{frame}.csv`
```csv
frame_idx,nframe,ts_sec,ts_usec,cam_time_ns,trigger_frame_idx
0,100,1234,567890,1234567890000,42
```
`trigger_frame_idx` is written to every row and indicates which buffer frame corresponds to the real `ZONE_ENTER` moment — that is, it marks **recording start**, not stimulus onset. Actual stimulus onset for opto/visual is in `latency.csv`'s `frame` field for that system's row (`"opto"`/`"visual"`); `record_frame` on that same row equals `trigger_frame_idx`/the outer entry frame, so `(row.frame - row.record_frame)` is the number of Braid frames between recording start and stimulus onset. Convert to camera frames via the fps ratio if needed for video alignment (Braid runs ~100Hz; camera fps is in `configs/config.toml`'s `[camera]` section).

**Lens timing CSV:** `{save_folder}/obj_id_{obj_id}_frame_{frame}_lens_timing.csv`

Written by the Python `LiquidLens` process. One row per commanded diopter change while tracking an object (updates that fall below the slew-rate threshold or arrive within the 25ms hardware rate limit are skipped and don't produce a row).
```csv
t_braid,t_relay,t_lens_recv,t_serial_start,t_diopter_sent,delay_ms,frame,obj_id,x,y,z,focus_z,diopter,target_diopter,predictor
1234567.888,1234567.889,1234567.890,1234567.891,1234567.893,2.1,4589,123,0.01,-0.02,0.18,0.18,3.2,3.2,kalman
```
`delay_ms` = time from `t_serial_start` to `t_diopter_sent`, i.e. the USB serial write itself. `predictor` records which mode (`none`, `linear`, `kalman`) produced `focus_z` for that row. `diopter` is the slew-rate-limited value actually sent to the lens; `target_diopter` is what the calibration curve returned before limiting. Compare the two to see how much `max_diopter_step` is holding back a given trial. Feed this file to `uv run python -m src.tools.lens_latency_analyze` for latency percentile breakdowns and a recommended `system_latency`.

**Debug histograms:** Generate offline with `src/tools/generate_camera_histograms.py`
- Reads CSV files and produces PNG histograms showing frame counter diffs, inter-frame interval, jitter, timeline
- Usage: `python src/tools/generate_camera_histograms.py /path/to/videos/`

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

**Binary not found** — `RustCameraProcess` searches these paths in order:
1. `optofly-camera/target/release/optofly-camera`
2. `optofly-camera/target/debug/optofly-camera`
3. `optofly-camera` on `PATH`

## Testing

```bash
# Integration test (requires hardware)
python tests/test_camera_integration.py

# Check Rust compilation
cd optofly-camera && cargo check
```

`test_camera_integration.py` exercises the pure-Python `CameraProcess` class in `src/processes/camera.py` (talks to the camera directly via `ximea-py`), not `RustCameraProcess`. The two are separate implementations in the same file. `main.py` runs `RustCameraProcess` (imported under the alias `CameraProcess`) for actual experiments, so passing this test does not confirm the Rust binary path works. Verify that by running `main.py` with `[camera] active = true` and checking a recording is produced.
