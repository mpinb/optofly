# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

Always use Context7 (`mcp__context7__resolve-library-id` + `mcp__context7__query-docs`) when you need library or API documentation, code generation examples, or setup/configuration steps for any dependency.

## Commands

```bash
# Run experiment
uv run python main.py

# Python tests
uv run pytest
uv run pytest tests/ -v               # verbose

# Lint
uv run ruff check .
uv run ruff format .

# Visual stimuli calibration
python -m src.processes.visual --calibrate           # identify screens
python -m src.processes.visual --calibrate-mapping   # heading → pixel mapping (manual x,y input)
python -m src.processes.visual --test-calibration    # verify with sweeping circle
python -m src.processes.visual --standalone          # test without hardware

# Simulate Braid tracking data (development)
python -m src.tools.braid_simulator
```

## Configuration

Copy and customize — local files are git-ignored:
```bash
cp configs/config.example.toml configs/config.toml
cp configs/visual_stimuli.example.toml configs/visual_stimuli.toml
```

`configs/config.toml` controls hardware (Braid URL, trigger zone, camera, opto, lens).
`configs/visual_stimuli.toml` controls display layout, screen mapping, stimuli parameters.
Each process checks its own `active = true/false` flag before starting.

## Architecture

### Data Flow

```
Braid HTTP SSE (http://host:8397/events)
    ↓
BraidPublisher  →  ZMQ PUB  topic=BRAID  port=5555
    ↓
TriggerHandler  →  ZMQ PUB  topics=PRE_ZONE_ENTER/PRE_ZONE_EXIT
                             topics=ZONE_ENTER/ZONE_EXIT        port=5556
    ↓
    ├── RustCameraProcess    (starts recording on PRE_ZONE_ENTER; stamps trigger_frame_idx on ZONE_ENTER)
    ├── LiquidLens           (starts pre-focusing on PRE_ZONE_ENTER via BRAID; writes lens_timing.csv per video)
    ├── OptoTriggerWorker    (fires LED on ZONE_ENTER only, one-shot)
    ├── VisualStimuliProcess (renders stimuli on ZONE_ENTER only, one-shot)
    └── Monitoring Server    (web dashboard, optional)
```

**Pre-trigger zone**: TriggerHandler maintains two concentric zones. The outer zone (camera FOV + `pre_zone_expansion` metres on every side) fires `PRE_ZONE_ENTER`/`PRE_ZONE_EXIT` so the camera and lens can get a head-start before the fly reaches the actual trigger zone. Opto and visual stimuli still fire only on `ZONE_ENTER`. Setting `pre_zone_expansion = 0` makes both zones identical, restoring the old single-zone behaviour.

The ZMQ BRAID feed is only live when the full stack is running. Standalone tools (calibration, simulators) that need tracking data must connect directly to the Braid HTTP SSE endpoint (`/events`).

### Process Model

All processes inherit `WorkerProcess` (`src/utils/worker.py`) and run as `multiprocessing.Process` instances. They:
- Accept a shared `mp.Event` for coordinated shutdown
- Initialize ZMQ sockets inside `_run()` (not `__init__`) to avoid fork issues
- Receive multipart ZMQ messages: `[topic_bytes, json_bytes]`

**Logging**: each child process calls `configure_process_logging()` (`src/utils/logger.py`) at entry inside `WorkerProcess.run()`, which clears all inherited root-logger handlers and attaches a fresh colored stream handler + optional file handler. Subclasses override `_run()`, not `run()`. Pass `log_path=` to the constructor to get per-process log files. `main.py` calls `configure_process_logging` directly for the main process.

### ZMQ Message Formats

**BRAID** (from BraidPublisher):
```json
{"Birth": {"obj_id": 1, "x": 0.0, "y": 0.0, "z": 0.0, ...}}
{"Update": {"obj_id": 1, "x": 0.01, "y": -0.02, "z": 0.18, "xvel": ..., ...}}
{"Death": {"obj_id": 1}}
```

**ZONE_ENTER / PRE_ZONE_ENTER** (from TriggerHandler — identical format):
```json
{"obj_id": 1, "frame": 12345, "timestamp": 1234.56, "x": 0.01, "y": -0.02, "z": 0.18, "mean_heading": 0.52}
```

**ZONE_EXIT / PRE_ZONE_EXIT** (from TriggerHandler — identical format):
```json
{"obj_id": 1, "reason": "left_fov", "timestamp": 1234.78, "duration": 0.22}
```

### Configuration Loading

`src/utils/config.py` has typed config classes (e.g. `LiquidLensConfig`, `ZMQConfig`) that load from TOML sections. Pass `config_path` to each process; don't read TOML directly elsewhere. `trigger_handler.zone_timeout` is the single global timeout used by TriggerHandler, CameraProcess (buffer sizing), and LiquidLens (focus tracking).

Key new parameters (all in `configs/config.toml`):

| Section | Key | Default | Purpose |
|---|---|---|---|
| `[trigger_handler]` | `pre_zone_expansion` | `0.0` | Metres added to each FOV edge and z bound for the pre-trigger zone |
| `[zmq]` | `pre_zone_enter_topic` | `"PRE_ZONE_ENTER"` | Topic for pre-zone entry events |
| `[zmq]` | `pre_zone_exit_topic` | `"PRE_ZONE_EXIT"` | Topic for pre-zone exit events |
| `[liquid_lens.kalman]` | `velocity_noise` | `1.0` | Measurement noise for Braid velocity estimates fed into the Kalman filter |

### Visual Stimuli

Plugin-based pattern in `src/stimuli/`. Included stimuli: `StaticPatternStimulus`, `LoomingStimulusRenderer`, `VerticalBarStimulus`. To add a stimulus:
1. Create class in `src/stimuli/my_stimulus.py` extending `BaseStimulus` with `on_trigger`, `update`, `render`, `is_active`
2. Register in `src/processes/visual.py:_initialize_stimuli()`
3. Add `[visual_stimuli.my_stimulus]` section to `configs/visual_stimuli.toml`

Heading-to-pixel conversion uses `GeometryUtils` (`src/stimuli/geometry.py`). With `use_empirical_calibration = true`, it interpolates from `calibrations/heading_mapping_model.npz`; otherwise it uses a geometric formula.

### Liquid Lens Calibration

`calibrations/liquid_lens.csv` maps `z` (meters) → `dpt` (diopters). Create it manually by stepping through z-heights and finding the in-focus diopter value with `src/hardware/lens.py:LensDriver`. See `calibrations/LIQUID_LENS_CALIBRATION.md`.

### Camera

`RustCameraProcess` (`src/processes/camera.py`) launches the `optofly-camera` Rust binary (`optofly-camera/`) as a subprocess. The binary captures frames via the XIMEA SDK (`xiapi` crate) into a linear double-buffer and pipes raw frames to ffmpeg for H.264 encoding (NVENC with x264 fallback). On shutdown, the Python wrapper sends a ZMQ `kill` message for graceful exit. Requires `ffmpeg` on PATH and the XIMEA SDK. Build: `cd optofly-camera && cargo build --release`.

State machine (Rust binary):
- **IDLE + `PRE_ZONE_ENTER`** → start recording; `trigger_frame_idx = None`
- **IDLE + `ZONE_ENTER`** → start recording (backward-compat path when `pre_zone_expansion = 0`); `trigger_frame_idx = 0`
- **RECORDING + `ZONE_ENTER`** → stamp `trigger_frame_idx = current_buf_idx`; log pre-trigger frame count
- **RECORDING + `PRE_ZONE_EXIT`** (no `ZONE_ENTER` seen) → abort recording
- **RECORDING + `ZONE_EXIT`** → finish and encode

Output files per trial (all in `camera.save_folder`):
- `obj_id_{N}_frame_{M}.mp4` — encoded video
- `obj_id_{N}_frame_{M}.csv` — per-frame metadata (`frame_idx`, `nframe`, `ts_sec`, `ts_usec`, `cam_time_ns`, `trigger_frame_idx`). `trigger_frame_idx` repeats on every row — it is the buffer index at which `ZONE_ENTER` fired, marking stimulus onset. Use it to align trials: frames before it are pre-stimulus baseline, frames after are the response.
- `obj_id_{N}_frame_{M}_lens_timing.csv` — per-adjustment lens timing (`t_braid_received`, `t_diopter_sent`, `delay_ms`, `z`, `diopter`, ...)

**`max_recording_time` vs `zone_timeout`**: `camera.max_recording_time` is a frame-buffer size limit — it counts from `PRE_ZONE_ENTER` (not `ZONE_ENTER`), so it must cover pre-zone transit time + trial duration. `trigger_handler.zone_timeout` is the tracker's dead-reckoning timeout for declaring a fly has left the zone. Set `max_recording_time` ≥ `zone_timeout` + expected pre-zone transit time.

### Liquid Lens

`LiquidLens` (`src/processes/lens.py`) responds to `PRE_ZONE_ENTER` to begin pre-focusing before the fly reaches the actual trigger zone, giving the Optotune lens time to settle. It subscribes to BRAID position updates and calls `LensDriver.set_diopter()` on every update while tracking.

**Kalman filter** (`src/utils/kalman_filter.py`): 6D state `[x, y, z, vx, vy, vz]`, DWNA process noise model. When enabled, predicts z position `system_latency + prediction_horizon` seconds ahead to compensate for lens settling time. Velocity from Braid is fused as a proper sequential measurement update (controlled by `velocity_noise`) rather than overwriting the state — keeping the covariance matrix consistent. Covariance updates use the Joseph form for numerical stability.
