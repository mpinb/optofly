# Ximea Camera

High-speed triggered video recording using the `optofly-camera` Rust binary.

**Specifications:**
- 500fps at 2112x2112 pixels (configurable)
- H.264 encoding with NVENC hardware acceleration (x264 fallback)
- Linear double-buffer design for zero-copy, race-free operation
- ~18GB memory footprint (default settings, 2 buffers)

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

**Double-buffer pattern:** Two pre-allocated `Vec<u8>` buffers sized for `max_recording_time + 1s`. On recording completion, the active buffer is taken (`Option::take`) and enqueued for encoding. The encoder reads the old buffer while capture continues into the new one.

**Memory:** `2 x (max_recording_time + 1s) x fps x width x height`
Default settings (3s, 500fps, 2112x2112): ~17.8GB total (~8.9GB per buffer).

### Python Wrapper (`src/processes/camera.py`)

`RustCameraProcess` extends `WorkerProcess` and:
- Locates the binary in `optofly-camera/target/{release,debug}/` or `PATH`
- Launches it with `--config`, `--save-folder`, and `--log-level` arguments
- Monitors the subprocess and the shared `stop_event`
- On shutdown: sends SIGTERM, waits up to 30s for graceful exit, then SIGKILL

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

for name, result in check_camera_prerequisites("configs/config.toml").items():
    print(name, result)
```

Returns a `dict[str, CheckResult]` covering `camera_binary`, `ffmpeg`,
`save_folder_writable`, and `trigger_port`. Each `CheckResult` has `.ok` (bool)
and `.detail` (what to do if it failed). A failing `trigger_port` just means the
experiment isn't running. See [troubleshooting.md](troubleshooting.md#runtime).

## Configuration

The Rust binary reads from the shared `configs/config.toml`:

```toml
[camera]
active = true
resolution = [2112, 2112]
fps = 500
exposure_time = 2000
max_recording_time = 3.0   # seconds, controls buffer size

# Rust-binary-only keys (all optional; ignored by Python):
# buffers_queue_size = 32   # XIMEA driver buffer queue depth
# aeag = false              # auto-exposure/gain (gain locked at 0 dB)
# aeag_level = 50           # AEAG target brightness level
# ae_max_limit = 1900.0     # max AE exposure in µs (default: 95% of frame period)

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

The binary also subscribes to a bare `kill` topic, but **nothing in this
codebase publishes to it** — shutdown is by SIGTERM from the Python wrapper.
The subscription is vestigial; don't build on it without adding a publisher.

**State machine:**

| State | Event | Action |
|-------|-------|--------|
| IDLE | `ZONE_ENTER` | → RECORDING; `trigger_frame_idx = 0` |
| RECORDING | `ZONE_EXIT` | finish and hand buffer to encoder |

## Output

**Video:** `{save_folder}/obj_id_{obj_id}_frame_{frame}.mp4`
- Codec: H.264 (NVENC p4/constqp18, or x264 ultrafast/crf18 fallback), grayscale input
- To review these high-speed recordings frame-by-frame, use **Avidemux** — it's already installed on this machine. If it's missing on another one, get it from
  [avidemux.sourceforge.net](https://avidemux.sourceforge.net/).

**Metadata CSV:** `{save_folder}/obj_id_{obj_id}_frame_{frame}.csv`
```csv
frame_idx,nframe,ts_sec,ts_usec,cam_time_ns,trigger_frame_idx,opto_frame_idx,visual_frame_idx
0,100,1234,567890,1234567890000,0,3,
```
`trigger_frame_idx` is written to every row and indicates which buffer frame corresponds to the real `ZONE_ENTER` moment — that is, it marks **recording start**, not stimulus onset. Since the capture buffer resets at `ZONE_ENTER`, it is always `0` (there are no pre-trigger frames).

`opto_frame_idx`/`visual_frame_idx` mark the buffer frame at which `OPTO_ZONE_ENTER`/`VISUAL_ZONE_ENTER` arrived on the ZMQ trigger socket — a frame-exact, live-captured stimulus-onset marker, with no fps-ratio math and no dropped-frame assumption. They live in this same per-video metadata CSV (`{save_folder}/obj_id_{obj_id}_frame_{frame}.csv`, next to that trial's `.mp4`) — there is no separate stimulus-data file per video. Each is written as a constant value across every row (same style as `trigger_frame_idx`) and is blank when that system never fired during the recording (system inactive, or the fly never reached the inner zone). This marks when the trigger broadcast reached the capture loop, not when the LED/stimulus physically actuated; add `(activation_timestamp - trigger_timestamp) × camera_fps` from `latency.csv` to get actuation onset.

**Lens timing CSV:** `{save_folder}/obj_id_{obj_id}_frame_{frame}_lens_timing.csv`

Written by the Python `LiquidLens` process. One row per commanded diopter change while tracking an object (updates that fall below the slew-rate threshold or arrive within the 25ms hardware rate limit are skipped and don't produce a row).
```csv
t_braid,t_relay,t_lens_recv,t_serial_start,t_diopter_sent,delay_ms,frame,obj_id,x,y,z,focus_z,diopter,target_diopter,predictor
1234567.888,1234567.889,1234567.890,1234567.891,1234567.893,2.1,4589,123,0.01,-0.02,0.18,0.18,3.2,3.2,linear
```
`delay_ms` = time from `t_serial_start` to `t_diopter_sent`, i.e. the USB serial write itself. `predictor` records which mode (`none` or `linear`) produced `focus_z` for that row. `diopter` is the slew-rate-limited value actually sent to the lens; `target_diopter` is what the calibration curve returned before limiting. Compare the two to see how much `max_diopter_step` is holding back a given trial. Feed this file to `uv run python -m src.tools.lens_latency_analyze` for latency percentile breakdowns and a recommended `system_latency`.

**Debug histograms:** Generate offline with `src/tools/generate_camera_histograms.py`
- Reads CSV files and produces PNG histograms showing frame counter diffs, inter-frame interval, jitter, timeline
- Usage: `python src/tools/generate_camera_histograms.py /path/to/videos/`

**Stimulus-onset → video-frame alignment:** `src/tools/frame_alignment.py`
- **For opto/visual, prefer `opto_frame_idx`/`visual_frame_idx` in the metadata CSV above** — they're exact, live-captured frame numbers with no fps-ratio approximation. The tool now surfaces the same value itself (as `live_frame_idx`, read straight from that CSV) alongside its own fps-ratio estimate, so for recordings made after this feature shipped you don't need to cross-reference the metadata CSV by hand.
- **`lens` has no comparable live marker, and its `latency.csv` delta is not currently meaningful**: `LiquidLens` publishes its `frame`/`record_frame` pair from the `ZONE_ENTER` snapshot rather than the frame the first diopter command was actually sent on, so the two are always equal and this tool's `video_frame`/`live_frame_idx` output for `lens` will always be `0`/blank. Use `lens_timing.csv`'s own `frame` column (see above) for lens timing per adjustment.
- For each `opto`/`visual` (or `lens`, via `--systems`) row in `latency.csv`, computes the corresponding video frame from `(row.frame - row.record_frame)` scaled by the camera/Braid fps ratio, joins it against the matching `obj_id_{obj_id}_frame_{record_frame}.csv` to report that video's actual frame count and (for opto/visual) its live `opto_frame_idx`/`visual_frame_idx`, and warns when the computed frame falls outside the video's frame count (recording ended — `zone_timeout`/buffer-full/`left_fov` — before the stimulus fired).
- Works directly against a completed recording's zipped `.braidz` file (reads `latency.csv` and `config.toml` — for `camera.fps` — as zip members) as well as a still-open or crashed/leftover raw `.braid` folder; no unzipping needed. Point it at the videos folder with `--video-folder` if it isn't the one auto-derived from `src/orchestration.py`'s layout (`<data_root>/videos/<name>.braid`, sibling of `<data_root>/experiments/`).
- `video_frame` is a frame-count approximation, not an exact timestamp lookup — the camera's own per-frame timestamps aren't on a clock synchronized with Braid's. `live_frame_idx` (opto/visual only, when present) is exact — prefer it when both are available.
- Usage: `uv run python -m src.tools.frame_alignment /mnt/data/experiments/<timestamp>.braidz` — prints one row per trigger to stdout as CSV (or write it out with `--output`):
```csv
obj_id,system,record_frame,braid_frame,braid_frame_delta,video_frame,live_frame_idx,video_csv,video_frame_count,sham,latency_ms
1,opto,12345,12350,5,25,26,obj_id_1_frame_12345.csv,100,False,12.4
```
  `video_frame` is the fps-ratio estimate; `live_frame_idx` (blank for `lens`, or for recordings made before this feature shipped) is the exact value read from `video_csv`'s `opto_frame_idx`/`visual_frame_idx` column — prefer it when present. `video_frame_count` (from the matching metadata CSV, blank if it wasn't found) is what `video_frame` is range-checked against; a warning is printed to stderr per row that falls outside it.
- Options: `--video-folder` (override the auto-derived videos folder), `--camera-fps` (skip reading `config.toml`, e.g. for recordings that predate config-copying; must be positive), `--braid-fps` (override the 100 Hz default; must be positive), `--systems` (comma-separated, default `opto,visual`; add `lens` to include liquid-lens triggers, though see the limitation above), `--output` (write the CSV to a file instead of stdout).

## Finding a video's stimulus parameters

A video's filename only gives you `obj_id` and `record_frame` — the Braid frame `ZONE_ENTER` fired on, i.e. recording start. That's not enough on its own to look up the LED or visual-stimulus parameters used for that trial, because `opto.csv`/`stim.csv` are keyed by `frame`, the Braid frame `OPTO_ZONE_ENTER`/`VISUAL_ZONE_ENTER` actually fired on. `frame` and `record_frame` only coincide when `opto_zone_scale`/`visual_zone_scale` is `1.0`; at the default of `0.8` they typically differ by a handful of Braid frames (see [Configuration](architecture.md#configuration-loading) in `docs/architecture.md`).

`latency.csv` is the bridge, since every row carries both `frame` and `record_frame` for the same trigger:

1. Parse `obj_id` and `record_frame` from the video filename (`obj_id_{obj_id}_frame_{record_frame}.mp4`).
2. Find the `latency.csv` row with that `obj_id`/`record_frame` and the system you want (`system` = `"opto"` or `"visual"`) — its `frame` column is the key into the next file.
3. Look up that `obj_id`/`frame` pair in `opto.csv` (LED parameters, see [docs/opto-trigger.md](opto-trigger.md)) or `stim.csv` (visual stimulus parameters, see [docs/visual-stimuli-panda3d.md](visual-stimuli-panda3d.md)).

**`latency.csv` columns:**
`obj_id, frame, record_frame, system, braid_timestamp, trigger_timestamp, activation_timestamp, latency_ms, sham`

`system` is `"opto"`, `"visual"`, or `"lens"`. `latency_ms = (activation_timestamp - braid_timestamp) * 1000`, blank for sham trials. Written solely by `LatencyLogger` (`src/processes/latency_logger.py`) — see the LATENCY message format in `docs/architecture.md` for the full wire-format description this file mirrors.

If you only need the exact video frame the stimulus fired on rather than its parameters, skip this lookup entirely — the per-video metadata CSV's `opto_frame_idx`/`visual_frame_idx` above already gives you that directly, no `latency.csv` join required.

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
# Python-side unit tests (no hardware)
uv run pytest tests/test_camera_config.py tests/test_camera_prerequisites.py

# Rust unit tests + compile check
cd optofly-camera && cargo test && cargo check
```

There is no automated end-to-end camera test: capture needs a real XIMEA device,
so nothing above proves the binary can actually record. Verify that by hand —
run `main.py` with `[camera] active = true` and confirm an
`obj_id_{N}_frame_{M}.mp4` appears in `camera.save_folder` after a trigger.

`RustCameraProcess` is the only camera implementation. An earlier pure-Python
`CameraProcess` that drove the sensor directly via `ximea-py` was removed;
`src/orchestration.py` imports `RustCameraProcess` under the alias
`CameraProcess`, which is all that name now refers to.
