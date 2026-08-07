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

# Liquid lens focusing latency report (recommends system_latency for [liquid_lens.kalman])
uv run python -m src.tools.lens_latency_analyze /mnt/data/videos/<braid_dir>

# Simulate Braid tracking data (development; binds the BRAID PUB port itself)
uv run python -m src.tools.braid_simulator

# Real-time tracking visualization (ReRun; consumes the ZMQ BRAID feed)
uv run python -m src.tools.braid_visualizer

# Braid↔XIMEA camera calibration (DLT; also drives frustum FOV point picking)
uv run python -m src.tools.calibrate_braid_ximea

# Camera CSV QA plots (frame-counter gaps, inter-frame intervals, jitter)
uv run python -m src.tools.generate_camera_histograms /mnt/data/videos/<braid_dir>

# Recover a crashed/leftover .braid folder into .braidz (stdlib only, no Rust toolchain needed)
uv run python scripts/braidz_writer.py /mnt/data/experiments/<timestamp>.braid

# Install/update the XIMEA camera driver (one-time hardware setup)
sudo scripts/install_ximea_driver.sh
```

## Configuration

Copy and customize — local files are git-ignored:
```bash
cp configs/config.example.toml configs/config.toml
cp configs/visual_stimuli.example.toml configs/visual_stimuli.toml
```

`configs/config.toml` controls hardware (Braid URL, trigger zone, camera, opto, lens).
`configs/visual_stimuli.toml` controls display layout, screen mapping, stimuli parameters.
Which processes start is gated by `active` flags in config — with two exceptions (`OptoTriggerWorker` always starts, `LiquidLens` rides on `camera.active`); see the process table under Architecture.

## Architecture

### Data Flow

```
Braid HTTP SSE (http://host:8397/events)
    ↓
BraidPublisher  →  ZMQ PUB  topic=BRAID         port=5555   (full stream)
                →  ZMQ PUB  topic=ACTIVE_BRAID  port=5557   (updates for the in-zone object only; gated by
                                                             ZONE_ENTER/ZONE_EXIT it SUBs to on port=5556)
    ↓
TriggerHandler (SUB 5555)  →  ZMQ PUB  topics=ZONE_ENTER/ZONE_EXIT/OPTO_ZONE_ENTER/VISUAL_ZONE_ENTER  port=5556
    ↓
    ├── RustCameraProcess    (starts recording on ZONE_ENTER; stamps trigger_frame_idx on ZONE_ENTER)
    ├── LiquidLens           (starts focusing on ZONE_ENTER via ACTIVE_BRAID; writes lens_timing.csv per video) ─┐
    ├── OptoTriggerWorker    (fires LED on OPTO_ZONE_ENTER, one-shot)                                     ├─→ ZMQ PUSH  port=latency_port ─→ LatencyLogger (ZMQ PULL, writes latency.csv)
    └── VisualProcess        (Panda3D; renders stimuli on VISUAL_ZONE_ENTER, one-shot)                    ─┘
```

The ZMQ BRAID feed is only live when the full stack is running. Standalone tools (calibration, simulators) that need tracking data must connect directly to the Braid HTTP SSE endpoint (`/events`) — with two exceptions: `braid_visualizer` consumes the ZMQ BRAID feed, and `braid_simulator` binds the BRAID PUB port itself, so the visualizer works against either the full stack or the simulator.

`ZONE_ENTER`/`ZONE_EXIT` still gate the camera and lens exactly as before — recording starts as soon as an object enters the outer trigger zone. `OPTO_ZONE_ENTER`/`VISUAL_ZONE_ENTER` are separate, one-shot events emitted by `TriggerHandler` only once a tracked object — already inside the outer `ZONE_ENTER` zone — reaches a smaller zone nested inside it, sized by `opto_zone_scale`/`visual_zone_scale` in `[trigger_handler]` (fraction of the outer FOV, centered). Setting either scale to `1.0` reproduces today's same-frame behavior for that system (fires on the same frame as `ZONE_ENTER`).

`LatencyLogger` is core and always-on (started right after `TriggerHandler`, before any optional process) — a dead `LatencyLogger` only loses latency data, it never aborts the experiment. It's the one place in the codebase using ZMQ PUSH/PULL instead of PUB/SUB: `OptoTriggerWorker`, `VisualProcess`, and `LiquidLens` each PUSH one `LATENCY` message per trigger to `zmq.latency_port` (a many-producer/one-consumer fan-in, not a broadcast), and `LatencyLogger` is the sole writer of `latency.csv` in the braid folder. Each `LATENCY` message carries `system` (`"opto"` | `"visual"` | `"lens"`), `obj_id`, `frame`, `record_frame`, `braid_timestamp`, `trigger_timestamp`, `activation_timestamp`, and `sham`; `LatencyLogger` computes `latency_ms = (activation_timestamp - braid_timestamp) * 1000` for non-sham trials. `LiquidLens` only publishes latency for the first commanded diopter per trial (not every subsequent tracking update).

### Orchestration

`main.py` is a thin CLI (`--config`, `--skip-metadata`) over `Experiment` (`src/orchestration.py`), which owns the full process lifecycle: `prepare_braid_folder()` (starts a fresh Braid recording via the callback API) → `collect_metadata()` (prompt; writes `experiment_data.toml` into the braid folder and appends a row to `~/optofly_experiments.csv`) → `start()` → poll `is_running()`/`check_health()` → `stop()` from a `finally`.

`Experiment.start()`:
- Copies `config.toml` (and `visual_stimuli.toml` when visual is active) into the braid folder, and attaches the main-process log to `optofly.log` there.
- Spawns in fixed order: `BraidPublisher` → `TriggerHandler` → `LatencyLogger` (each separated by a 0.5 s stagger) → optional processes → `OptoTriggerWorker` (always last, no inter-process stagger after `LatencyLogger`).
- Waits 1 s, then treats a dead critical process as `ExperimentStartError`, quoting the child's own reported exception (workers report crashes through an `mp.Queue` failure channel) before falling back to the static per-process hints in `_CRITICAL_INIT_HINTS`.

`check_health()` is the mid-run equivalent: a critical death sets the stop event (fatal); a non-critical death is logged once and the run continues. `stop()` signals the shared event, joins each process (`_SHUTDOWN_TIMEOUTS`: 35 s for the camera, 5 s default; `terminate()` fallback), verifies Braid CSVs, and stops the Braid recording. `main.py` also warns at startup when `camera.max_recording_time < trigger_handler.zone_timeout`.

### Process Model

All worker processes inherit `WorkerProcess` (`src/utils/worker.py`) and run as `multiprocessing.Process` instances. They:
- Accept a shared `mp.Event` for coordinated shutdown
- Initialize ZMQ sockets inside `_run()` (not `__init__`) to avoid fork issues
- Receive multipart ZMQ messages: `[topic_bytes, json_bytes]` (exception: the LATENCY PUSH/PULL channel is a single JSON frame, no topic prefix)

**Logging**: each child process calls `configure_process_logging()` (`src/utils/logger.py`) at entry inside `WorkerProcess.run()`, which clears all inherited root-logger handlers and attaches a fresh colored stream handler + optional file handler. Subclasses override `_run()`, not `run()`. Pass `log_path=` to the constructor to get per-process log files. `Experiment.start()` (`src/orchestration.py`) calls it for the main process, writing `optofly.log` into the braid folder.

### ZMQ Message Formats

**BRAID** (from BraidPublisher):
```json
{"Birth": {"obj_id": 1, "x": 0.0, "y": 0.0, "z": 0.0, ...}}
{"Update": {"obj_id": 1, "x": 0.01, "y": -0.02, "z": 0.18, "xvel": ..., ...}}
{"Death": 1}
```
Death carries the bare obj_id as an integer, not an object. Before publishing, `BraidPublisher` injects two fields into every Birth/Update payload: `t_relay` (its own wall-clock receipt time, used by the lens timing pipeline) and `braid_timestamp` (the Triggerbox-clock timestamp from Braid's SSE envelope, `None` when absent). The `ACTIVE_BRAID` feed on port 5557 carries the same Update payloads (injected fields included), but only for the object currently inside the trigger zone.

**ZONE_ENTER** (from TriggerHandler):
```json
{"obj_id": 1, "frame": 12345, "record_frame": 12345, "timestamp": 1234.56, "braid_timestamp": 1234.50, "handler_timestamp": 1234.56, "x": 0.01, "y": -0.02, "z": 0.18, "xvel": 0.05, "yvel": -0.12, "zvel": 0.01, "mean_heading": 0.52}
```
`timestamp`/`handler_timestamp` are both the handler's local receipt-time clock (used for velocity/age/cooldown math); `braid_timestamp` is the Triggerbox-clock-model timestamp from Braid's SSE envelope, kept separate since it's on a different clock and is only used for latency measurement (see `LatencyLogger` below). It's `None` if Braid didn't supply one for that sample. `record_frame` is the Braid frame of the outer `ZONE_ENTER` for this occupancy — equal to `frame` on `ZONE_ENTER` itself, but earlier than `frame` on the one-shot `OPTO_ZONE_ENTER`/`VISUAL_ZONE_ENTER` events (which share this payload shape), letting consumers measure recording-start → stimulus-onset offset.

**ZONE_EXIT** (from TriggerHandler):
```json
{"obj_id": 1, "reason": "left_fov", "timestamp": 1234.78, "duration": 0.22}
```

### Configuration Loading

`src/utils/config.py` has typed config classes (e.g. `LiquidLensConfig`, `ZMQConfig`) that load from TOML sections. Pass `config_path` to each process; don't read TOML directly elsewhere. `trigger_handler.zone_timeout` is the tracker's dead-reckoning timeout, used by TriggerHandler (declaring a fly has left the zone) and by BraidPublisher (expiring the ACTIVE_BRAID active object when zone events stop). Camera buffer sizing uses `camera.max_recording_time` instead; `LiquidLensConfig.zone_timeout` exists but is currently unused by `src/processes/lens.py`.

Each config class has exactly two constructors, and no others:

- `Cls.from_section(section_dict, ...)` — builds from an already-parsed TOML table. Called only by `AppConfig.load()`, which passes in the dependencies (`camera`, `zmq`, `trigger_handler`) explicitly so no config class ever constructs another.
- `Cls.from_path(config_path)` — convenience for standalone tools and processes that need one section. Delegates to `AppConfig.load()` and returns the corresponding attribute.

Both end at the dataclass-generated `__init__`, so **the declared fields are the construction interface**: add a field to the dataclass and forget it in `from_section()` and you get a `TypeError` naming the field at config-load time, not an `AttributeError` inside a child process at trigger time. Never construct these via `object.__new__` + `__dict__` assignment — that was the previous pattern and it made the two lists drift silently. `tests/test_config_construction.py` pins this.

`AppConfig.load()` builds and validates all eight config sections in one pass — regardless of any given section's own `active` flag. This means `configs/config.toml` must always have valid `[liquid_lens]`, `[opto_trigger]`, etc. sections present (each with its required `port` key) even when that subsystem is disabled via `active = false`, and even if you only ever need a single section (e.g. `ZMQConfig.from_path(path)`). Standalone tools that only need one section (e.g. `src/tools/braid_visualizer.py`, `src/tools/braid_simulator.py`) still need a fully valid config file for this reason.

Every config is frozen except `OptoTriggerConfig`, which `OptoTrigger.set_parameters()` mutates once per trigger to record the balanced-randomization-selected trial parameters.

Key parameters (all in `configs/config.toml`):

| Section | Key | Default | Purpose |
|---|---|---|---|
| `[zmq]` | `latency_port` | `5558` | PUSH/PULL port `OptoTriggerWorker`/`VisualProcess`/`LiquidLens` push `LATENCY` messages to; `LatencyLogger` binds it and writes `latency.csv` |
| `[zmq]` | `active_braid_port` | `5557` | PUB port for the `ACTIVE_BRAID` fast lane (only the in-zone object's updates); consumed by `LiquidLens` |
| `[zmq]` | `lens_update_conflate` | `true` | Sets `RCVHWM=1` on the lens's `ACTIVE_BRAID` socket to bound its queue depth (not `CONFLATE` — that option doesn't support multi-part messages and silently drops everything when combined with this feed's topic-filtered SUBSCRIBE; `LiquidLens._get_latest_active_update()` already drains-to-latest on every read) |
| `[zmq]` | `transport` | `"tcp"` | `"tcp"` (`tcp://localhost:PORT`) or `"ipc"` (per-port socket files under `/tmp`); used by `get_publisher_address`/`get_subscriber_address` |
| `[liquid_lens]` | `predictor` | `"none"` | `"none"` uses raw Braid z; `"linear"` extrapolates `z + vz * (system_latency + prediction_horizon)` |
| `[liquid_lens]` | `max_diopter_step` | `0.0` | Per-update slew-rate limit on commanded diopter; `0.0` disables it. Ramps large jumps (esp. trial onset) so the lens's ~400 Hz resonance isn't excited |
| `[liquid_lens.kalman]` | `system_latency` | `0.05` | Measured message + serial write delay (seconds); calibrate with `lens_latency_analyze`. Section name is legacy (predates removal of a Kalman-filter predictor mode) — still used by the `linear` predictor |
| `[liquid_lens.kalman]` | `prediction_horizon` | `0.05` | Additional lookahead beyond `system_latency` (seconds) |
| `[opto_trigger]` | `sham_probability` | `0.0` | Fraction of triggers that become sham trials (no LED pulse; recorded as `sham` in `opto.csv`/`latency.csv`) |
| `[camera]` | `aeag`, `aeag_level`, `ae_max_limit` | `false`, `50`, 95% of frame period | Rust-binary-only auto-exposure/gain (AEAG) settings; read by `optofly-camera`, ignored by Python. `buffers_queue_size` (default `32`) is likewise Rust-only |

`LiquidLens` also enforces a hardware floor of 25ms between serial commands regardless of `predictor` or `max_diopter_step` (~40 Hz max update rate).

### Which processes are started, and which failures are fatal

Not every process has an `active` flag, and criticality is derived from config rather than fixed:

| Process | Started when | Death is fatal when |
|---|---|---|
| `BraidPublisher` | always | always |
| `TriggerHandler` | always | always |
| `LatencyLogger` | always | never (only latency data is lost) |
| `VisualProcess` | `visual_stimuli.active` | never |
| `CameraProcess` | `camera.active` | never |
| `LiquidLens` | **`camera.active`** — it has no `active` flag of its own, since autofocus is only meaningful while the camera records | `camera.active` |
| `OptoTriggerWorker` | always — it also drives the backlight | **`opto_trigger.active`** only |

The two bolded rows are the surprising ones. Searching for `[liquid_lens] active` to disable the lens will find nothing; disable the camera instead. And `OptoTriggerWorker` is spawned even with `opto_trigger.active = false`, but a hardware failure there is then survivable — the process keeps running without the backlight rather than aborting a rig that has no Arduino attached. See `_critical_names()` in `src/orchestration.py`.

### Visual Stimuli (Panda3D)

Plugin-based pattern in `src/visual/stimuli/`. Included stimuli: `BackgroundStimulus`, `LoomingStimulus`, `OscillatingSquare`. The process is `VisualProcess` (`src/visual/process.py`). To add a stimulus:
1. Create class in `src/visual/stimuli/my_stimulus.py` extending `BaseStimulus` (`src/visual/base.py`) with `setup`, `on_trigger`, `update`
2. Register in `src/visual/process.py:_initialize_stimuli()`
3. Add `[visual_stimuli.my_stimulus]` section to `configs/visual_stimuli.toml`

Coordinate system: fly at origin, North=+Y, East=+X, Z=up, units in cm. Use `angular_to_world_pos` and `angular_size_to_radius` from `src/visual/base.py` for all position/size math. Arena heading alignment is set via `braid_heading_offset_rad` and `braid_heading_flip` in `[visual_stimuli.arena]`.

### Liquid Lens Calibration

`calibrations/liquid_lens.csv` maps `z` (meters) → `dpt` (diopters), fitted at startup by `LensCalibration` (`src/processes/lens.py`) using `calibration_model` (`linear` | `quadratic` | `power` | `inverse`). The loader expects exactly two columns, `z` and `dpt`. Build the CSV manually by stepping through z-heights with `optotune_lens.Lens`, or automate it with the separate [`liquid-lens-calibration`](https://github.com/mpinb/liquid-lens-calibration) repo (XIMEA + multi-Basler AprilTag triangulation); its output uses a `diopter` column, which needs renaming to `dpt` before use. See `docs/calibration.md`.

### Camera

`RustCameraProcess` (`src/processes/camera.py`) launches the `optofly-camera` Rust binary (`optofly-camera/`) as a subprocess. The binary captures frames via the XIMEA SDK (`xiapi` crate) into a linear double-buffer and pipes raw frames to ffmpeg for H.264 encoding (NVENC with x264 fallback). On shutdown, the Python wrapper sends SIGTERM to the subprocess for graceful exit (the binary also subscribes to a ZMQ `kill` topic, but nothing in this codebase currently publishes to it). Requires `ffmpeg` on PATH and the XIMEA SDK. Build: `cd optofly-camera && cargo build --release`. `src/orchestration.py` imports this class under the alias `CameraProcess`.

State machine (Rust binary):
- **IDLE + `ZONE_ENTER`** → start recording; `trigger_frame_idx = 0`
- **RECORDING + `ZONE_EXIT`** → finish and encode

Output files per trial (all in `camera.save_folder`):
- `obj_id_{N}_frame_{M}.mp4` — encoded video
- `obj_id_{N}_frame_{M}.csv` — per-frame metadata (`frame_idx`, `nframe`, `ts_sec`, `ts_usec`, `cam_time_ns`, `trigger_frame_idx`). `trigger_frame_idx` repeats on every row — it is the buffer index at which `ZONE_ENTER` fired, marking **recording start**, not stimulus onset. Actual stimulus onset for opto/visual is in `latency.csv`'s `frame` field for that system's row (`"opto"`/`"visual"`); `record_frame` on that same row is the Braid frame at which the outer `ZONE_ENTER` fired — the same moment `trigger_frame_idx` marks, but on the Braid frame counter — so `(row.frame - row.record_frame)` is the number of Braid frames between recording start and stimulus onset — convert to camera frames via the fps ratio if needed for video alignment (Braid runs ~100Hz; camera fps is in `configs/config.toml`'s `[camera]` section).
- `obj_id_{N}_frame_{M}_lens_timing.csv`: per-adjustment lens timing (`t_braid`, `t_relay`, `t_lens_recv`, `t_serial_start`, `t_diopter_sent`, `delay_ms`, `z`, `focus_z`, `diopter`, `target_diopter`, `predictor`, ...)

**`max_recording_time` vs `zone_timeout`**: `camera.max_recording_time` is a frame-buffer size limit — it counts from `ZONE_ENTER`. `trigger_handler.zone_timeout` is the tracker's dead-reckoning timeout for declaring a fly has left the zone. Set `max_recording_time` ≥ `zone_timeout`.

### Liquid Lens

`LiquidLens` (`src/processes/lens.py`) responds to `ZONE_ENTER` and subscribes to the `ACTIVE_BRAID` feed, calling `LensDriver.set_diopter()` while tracking. Three filters gate each command: the `predictor` mode (`"none"` or `"linear"`) picks the target z, `max_diopter_step` optionally slew-limits the jump from the last commanded diopter, and a command is dropped outright if it arrives less than 25ms after the last one or changes the diopter by under `1e-5`.

## Installing and Using Claude Code

Claude Code is an AI-powered coding assistant that runs in your terminal.

### Install

Pick one:

```bash
# macOS, Linux, or WSL
curl -fsSL https://claude.ai/install.sh | bash

# npm (any platform, requires Node.js 22+)
npm install -g @anthropic-ai/claude-code

# Homebrew (macOS)
brew install --cask claude-code
```

Windows PowerShell: `irm https://claude.ai/install.ps1 | iex`

Verify: `claude --version` should print a version like `2.1.X (Claude Code)`.

### First run

```bash
cd ~/src/OptoFly
claude
```

The first run opens a browser to authenticate (Claude Pro/Max, Team/Enterprise, Console, or an `ANTHROPIC_API_KEY` environment variable). Credentials are then stored locally — no repeat login.

If this repo's `CLAUDE.md` is ever missing, running `/init` inside a Claude Code session regenerates it from the current codebase.

### Useful commands

- `/help` — list all commands
- `/clear` — reset conversation history
- Shift+Tab — cycle permission mode (`plan` / default / `acceptEdits`)
