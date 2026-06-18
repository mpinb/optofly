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

# Visual stimuli (Panda3D) standalone test
python -m src.processes.visual --standalone          # small 1280×320 window, no hardware/ZMQ

# Legacy pyglet calibration tools (heading → pixel mapping)
python -m src.processes.visual --calibrate           # identify screens
python -m src.processes.visual --calibrate-mapping   # heading → pixel mapping (manual x,y input)
python -m src.processes.visual --test-calibration    # verify with sweeping circle

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
TriggerHandler  →  ZMQ PUB  topics=ZONE_ENTER/ZONE_EXIT  port=5556
    ↓
    ├── RustCameraProcess    (starts recording on ZONE_ENTER; stamps trigger_frame_idx on ZONE_ENTER)
    ├── LiquidLens           (starts focusing on ZONE_ENTER via BRAID; writes lens_timing.csv per video)
    ├── OptoTriggerWorker    (fires LED on ZONE_ENTER, one-shot)
    ├── VisualProcess        (Panda3D; renders stimuli on ZONE_ENTER, one-shot)
    └── Monitoring Server    (web dashboard, optional)
```

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

**ZONE_ENTER** (from TriggerHandler):
```json
{"obj_id": 1, "frame": 12345, "timestamp": 1234.56, "x": 0.01, "y": -0.02, "z": 0.18, "mean_heading": 0.52}
```

**ZONE_EXIT** (from TriggerHandler):
```json
{"obj_id": 1, "reason": "left_fov", "timestamp": 1234.78, "duration": 0.22}
```

### Configuration Loading

`src/utils/config.py` has typed config classes (e.g. `LiquidLensConfig`, `ZMQConfig`) that load from TOML sections. Pass `config_path` to each process; don't read TOML directly elsewhere. `trigger_handler.zone_timeout` is the single global timeout used by TriggerHandler, CameraProcess (buffer sizing), and LiquidLens (focus tracking).

Key parameters (all in `configs/config.toml`):

| Section | Key | Default | Purpose |
|---|---|---|---|
| `[liquid_lens.kalman]` | `velocity_noise` | `1.0` | Measurement noise for Braid velocity estimates fed into the Kalman filter |

### Visual Stimuli (Panda3D)

Plugin-based pattern in `src/visual/stimuli/`. Included stimuli: `BackgroundStimulus`, `LoomingStimulus`, `OscillatingSquare`. The process is `VisualProcess` (`src/visual/process.py`). To add a stimulus:
1. Create class in `src/visual/stimuli/my_stimulus.py` extending `BaseStimulus` (`src/visual/base.py`) with `setup`, `on_trigger`, `update`
2. Register in `src/visual/process.py:_initialize_stimuli()`
3. Add `[visual_stimuli.my_stimulus]` section to `configs/visual_stimuli.toml`

Coordinate system: fly at origin, North=+Y, East=+X, Z=up, units in cm. Use `angular_to_world_pos` and `angular_size_to_radius` from `src/visual/base.py` for all position/size math. Arena heading alignment is set via `braid_heading_offset_rad` and `braid_heading_flip` in `[visual_stimuli.arena]`.

The legacy pyglet pipeline (`src/stimuli/`) is still present for the `--calibrate*` CLI tools but is not used by `main.py`.

### Liquid Lens Calibration

`calibrations/liquid_lens.csv` maps `z` (meters) → `dpt` (diopters). Create it manually by stepping through z-heights and finding the in-focus diopter value with `src/hardware/lens.py:LensDriver`. See `calibrations/LIQUID_LENS_CALIBRATION.md`.

### Camera

`RustCameraProcess` (`src/processes/camera.py`) launches the `optofly-camera` Rust binary (`optofly-camera/`) as a subprocess. The binary captures frames via the XIMEA SDK (`xiapi` crate) into a linear double-buffer and pipes raw frames to ffmpeg for H.264 encoding (NVENC with x264 fallback). On shutdown, the Python wrapper sends a ZMQ `kill` message for graceful exit. Requires `ffmpeg` on PATH and the XIMEA SDK. Build: `cd optofly-camera && cargo build --release`.

State machine (Rust binary):
- **IDLE + `ZONE_ENTER`** → start recording; `trigger_frame_idx = 0`
- **RECORDING + `ZONE_EXIT`** → finish and encode

Output files per trial (all in `camera.save_folder`):
- `obj_id_{N}_frame_{M}.mp4` — encoded video
- `obj_id_{N}_frame_{M}.csv` — per-frame metadata (`frame_idx`, `nframe`, `ts_sec`, `ts_usec`, `cam_time_ns`, `trigger_frame_idx`). `trigger_frame_idx` repeats on every row — it is the buffer index at which `ZONE_ENTER` fired, marking stimulus onset. Use it to align trials: frames before it are pre-stimulus baseline, frames after are the response.
- `obj_id_{N}_frame_{M}_lens_timing.csv` — per-adjustment lens timing (`t_serial_start`, `t_diopter_sent`, `delay_ms`, `z`, `diopter`, ...)

**`max_recording_time` vs `zone_timeout`**: `camera.max_recording_time` is a frame-buffer size limit — it counts from `ZONE_ENTER`. `trigger_handler.zone_timeout` is the tracker's dead-reckoning timeout for declaring a fly has left the zone. Set `max_recording_time` ≥ `zone_timeout`.

### Liquid Lens

`LiquidLens` (`src/processes/lens.py`) responds to `ZONE_ENTER` and subscribes to BRAID position updates, calling `LensDriver.set_diopter()` on every update while tracking.

**Kalman filter** (`src/utils/kalman_filter.py`): 6D state `[x, y, z, vx, vy, vz]`, DWNA process noise model. When enabled, predicts z position `system_latency + prediction_horizon` seconds ahead to compensate for lens settling time. Velocity from Braid is fused as a proper sequential measurement update (controlled by `velocity_noise`) rather than overwriting the state — keeping the covariance matrix consistent. Covariance updates use the Joseph form for numerical stability.
