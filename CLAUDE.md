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
uv run python -m src.visual --standalone             # small 1280×320 window, no hardware/ZMQ

# Panda3D heading calibration (fits braid_heading_offset_rad / braid_heading_flip)
uv run python -m src.tools.calibrate_heading

# Frustum FOV calibration (near/far z planes for camera.FOV)
uv run python -m src.tools.calibrate_frustum_fov

# Liquid lens focusing latency report (recommends system_latency for [liquid_lens.kalman])
uv run python -m src.tools.lens_latency_analyze /mnt/data/videos/<braid_dir>

# Simulate Braid tracking data (development)
uv run python -m src.tools.braid_simulator

# Real-time tracking visualization (ReRun)
uv run python -m src.tools.braid_visualizer
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
    ├── LiquidLens           (starts focusing on ZONE_ENTER via BRAID; writes lens_timing.csv per video) ─┐
    ├── OptoTriggerWorker    (fires LED on ZONE_ENTER, one-shot)                                          ├─→ ZMQ PUSH  port=latency_port ─→ LatencyLogger (ZMQ PULL, writes latency.csv)
    ├── VisualProcess        (Panda3D; renders stimuli on ZONE_ENTER, one-shot)                           ─┘
    └── Monitoring Server    (web dashboard, optional)
```

The ZMQ BRAID feed is only live when the full stack is running. Standalone tools (calibration, simulators) that need tracking data must connect directly to the Braid HTTP SSE endpoint (`/events`).

`LatencyLogger` is core and always-on (started right after `TriggerHandler`, before any optional process) — a dead `LatencyLogger` only loses latency data, it never aborts the experiment. It's the one place in the codebase using ZMQ PUSH/PULL instead of PUB/SUB: `OptoTriggerWorker`, `VisualProcess`, and `LiquidLens` each PUSH one `LATENCY` message per trigger to `zmq.latency_port` (a many-producer/one-consumer fan-in, not a broadcast), and `LatencyLogger` is the sole writer of `latency.csv` in the braid folder. Each `LATENCY` message carries `system` (`"opto"` | `"visual"` | `"lens"`), `obj_id`, `frame`, `braid_timestamp`, `activation_timestamp`, and `sham`; `LatencyLogger` computes `latency_ms = (activation_timestamp - braid_timestamp) * 1000` for non-sham trials. `LiquidLens` only publishes latency for the first commanded diopter per trial (not every subsequent tracking update).

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
{"Death": 1}
```
Death carries the bare obj_id as an integer, not an object.

**ZONE_ENTER** (from TriggerHandler):
```json
{"obj_id": 1, "frame": 12345, "timestamp": 1234.56, "braid_timestamp": 1234.50, "handler_timestamp": 1234.56, "x": 0.01, "y": -0.02, "z": 0.18, "xvel": 0.05, "yvel": -0.12, "zvel": 0.01, "mean_heading": 0.52}
```
`timestamp`/`handler_timestamp` are both the handler's local receipt-time clock (used for velocity/age/cooldown math); `braid_timestamp` is the Triggerbox-clock-model timestamp from Braid's SSE envelope, kept separate since it's on a different clock and is only used for latency measurement (see `LatencyLogger` below). It's `None` if Braid didn't supply one for that sample.

**ZONE_EXIT** (from TriggerHandler):
```json
{"obj_id": 1, "reason": "left_fov", "timestamp": 1234.78, "duration": 0.22}
```

### Configuration Loading

`src/utils/config.py` has typed config classes (e.g. `LiquidLensConfig`, `ZMQConfig`) that load from TOML sections. Pass `config_path` to each process; don't read TOML directly elsewhere. `trigger_handler.zone_timeout` is the single global timeout used by TriggerHandler, CameraProcess (buffer sizing), and LiquidLens (focus tracking).

Every `*Config` class's path-based constructor routes through `AppConfig.load()`, which builds and validates all nine config sections in one pass — regardless of any given section's own `active` flag. This means `configs/config.toml` must always have valid `[liquid_lens]`, `[opto_trigger]`, etc. sections present (each with its required `port` key) even when that subsystem is disabled via `active = false`, and even if you only ever construct a single config class (e.g. `ZMQConfig(path)`). Standalone tools that only need one section (e.g. `src/tools/braid_visualizer.py`, `src/tools/braid_simulator.py`) still need a fully valid config file for this reason.

Key parameters (all in `configs/config.toml`):

| Section | Key | Default | Purpose |
|---|---|---|---|
| `[zmq]` | `latency_port` | `5558` | PUSH/PULL port `OptoTriggerWorker`/`VisualProcess`/`LiquidLens` push `LATENCY` messages to; `LatencyLogger` binds it and writes `latency.csv` |
| `[liquid_lens]` | `predictor` | `"none"` | `"none"` uses raw Braid z; `"linear"` extrapolates `z + vz * (system_latency + prediction_horizon)` |
| `[liquid_lens]` | `max_diopter_step` | `0.0` | Per-update slew-rate limit on commanded diopter; `0.0` disables it. Ramps large jumps (esp. trial onset) so the lens's ~400 Hz resonance isn't excited |
| `[liquid_lens.kalman]` | `system_latency` | `0.05` | Measured message + serial write delay (seconds); calibrate with `lens_latency_analyze`. Section name is legacy (predates removal of a Kalman-filter predictor mode) — still used by the `linear` predictor |
| `[liquid_lens.kalman]` | `prediction_horizon` | `0.05` | Additional lookahead beyond `system_latency` (seconds) |

`LiquidLens` also enforces a hardware floor of 25ms between serial commands regardless of `predictor` or `max_diopter_step` (~40 Hz max update rate).

### Visual Stimuli (Panda3D)

Plugin-based pattern in `src/visual/stimuli/`. Included stimuli: `BackgroundStimulus`, `LoomingStimulus`, `OscillatingSquare`. The process is `VisualProcess` (`src/visual/process.py`). To add a stimulus:
1. Create class in `src/visual/stimuli/my_stimulus.py` extending `BaseStimulus` (`src/visual/base.py`) with `setup`, `on_trigger`, `update`
2. Register in `src/visual/process.py:_initialize_stimuli()`
3. Add `[visual_stimuli.my_stimulus]` section to `configs/visual_stimuli.toml`

Coordinate system: fly at origin, North=+Y, East=+X, Z=up, units in cm. Use `angular_to_world_pos` and `angular_size_to_radius` from `src/visual/base.py` for all position/size math. Arena heading alignment is set via `braid_heading_offset_rad` and `braid_heading_flip` in `[visual_stimuli.arena]`.

### Liquid Lens Calibration

`calibrations/liquid_lens.csv` maps `z` (meters) → `dpt` (diopters), fitted at startup by `LensCalibration` (`src/processes/lens.py`) using `calibration_model` (`linear` | `quadratic` | `power` | `inverse`). The loader expects exactly two columns, `z` and `dpt`. Build the CSV manually by stepping through z-heights with `optotune_lens.Lens`, or automate it with the separate [`liquid-lens-calibration`](https://github.com/elhananby/liquid-lens-calibration) repo (XIMEA + multi-Basler AprilTag triangulation); its output uses a `diopter` column, which needs renaming to `dpt` before use. See `docs/calibration.md`.

### Camera

`RustCameraProcess` (`src/processes/camera.py`) launches the `optofly-camera` Rust binary (`optofly-camera/`) as a subprocess. The binary captures frames via the XIMEA SDK (`xiapi` crate) into a linear double-buffer and pipes raw frames to ffmpeg for H.264 encoding (NVENC with x264 fallback). On shutdown, the Python wrapper sends SIGTERM to the subprocess for graceful exit (the binary also subscribes to a ZMQ `kill` topic, but nothing in this codebase currently publishes to it). Requires `ffmpeg` on PATH and the XIMEA SDK. Build: `cd optofly-camera && cargo build --release`. `main.py` imports this class under the alias `CameraProcess`.

State machine (Rust binary):
- **IDLE + `ZONE_ENTER`** → start recording; `trigger_frame_idx = 0`
- **RECORDING + `ZONE_EXIT`** → finish and encode

Output files per trial (all in `camera.save_folder`):
- `obj_id_{N}_frame_{M}.mp4` — encoded video
- `obj_id_{N}_frame_{M}.csv` — per-frame metadata (`frame_idx`, `nframe`, `ts_sec`, `ts_usec`, `cam_time_ns`, `trigger_frame_idx`). `trigger_frame_idx` repeats on every row — it is the buffer index at which `ZONE_ENTER` fired, marking stimulus onset. Use it to align trials: frames before it are pre-stimulus baseline, frames after are the response.
- `obj_id_{N}_frame_{M}_lens_timing.csv`: per-adjustment lens timing (`t_braid`, `t_relay`, `t_lens_recv`, `t_serial_start`, `t_diopter_sent`, `delay_ms`, `z`, `focus_z`, `diopter`, `target_diopter`, `predictor`, ...)

**`max_recording_time` vs `zone_timeout`**: `camera.max_recording_time` is a frame-buffer size limit — it counts from `ZONE_ENTER`. `trigger_handler.zone_timeout` is the tracker's dead-reckoning timeout for declaring a fly has left the zone. Set `max_recording_time` ≥ `zone_timeout`.

### Liquid Lens

`LiquidLens` (`src/processes/lens.py`) responds to `ZONE_ENTER` and subscribes to the `ACTIVE_BRAID` feed, calling `LensDriver.set_diopter()` while tracking. Three filters gate each command: the `predictor` mode (`"none"` or `"linear"`) picks the target z, `max_diopter_step` optionally slew-limits the jump from the last commanded diopter, and a command is dropped outright if it arrives less than 25ms after the last one or changes the diopter by under `1e-5`.
