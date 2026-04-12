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
TriggerHandler  →  ZMQ PUB  topics=ZONE_ENTER/ZONE_EXIT  port=5556
    ↓
    ├── CameraProcess        (records while fly is in zone, via ximea-py + ffmpeg)
    ├── OptoTriggerWorker    (fires LED on ZONE_ENTER, one-shot)
    ├── VisualStimuliProcess (renders stimuli on ZONE_ENTER, one-shot)
    ├── LiquidLens           (tracks focus ZONE_ENTER→ZONE_EXIT via BRAID)
    └── Monitoring Server    (web dashboard, optional)
```

The ZMQ BRAID feed is only live when the full stack is running. Standalone tools (calibration, simulators) that need tracking data must connect directly to the Braid HTTP SSE endpoint (`/events`).

### Process Model

All processes inherit `WorkerProcess` (`src/utils/worker.py`) and run as `multiprocessing.Process` instances. They:
- Accept a shared `mp.Event` for coordinated shutdown
- Initialize ZMQ sockets inside `run()` (not `__init__`) to avoid fork issues
- Receive multipart ZMQ messages: `[topic_bytes, json_bytes]`

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

### Visual Stimuli

Plugin-based pattern in `src/stimuli/`. Included stimuli: `StaticPatternStimulus`, `LoomingStimulusRenderer`, `VerticalBarStimulus`. To add a stimulus:
1. Create class in `src/stimuli/my_stimulus.py` extending `BaseStimulus` with `on_trigger`, `update`, `render`, `is_active`
2. Register in `src/processes/visual.py:_initialize_stimuli()`
3. Add `[visual_stimuli.my_stimulus]` section to `configs/visual_stimuli.toml`

Heading-to-pixel conversion uses `GeometryUtils` (`src/stimuli/geometry.py`). With `use_empirical_calibration = true`, it interpolates from `calibrations/heading_mapping_model.npz`; otherwise it uses a geometric formula.

### Liquid Lens Calibration

`calibrations/liquid_lens.csv` maps `z` (meters) → `dpt` (diopters). Create it manually by stepping through z-heights and finding the in-focus diopter value with `src/hardware/lens.py:LensDriver`. See `calibrations/LIQUID_LENS_CALIBRATION.md`.

### Camera

`CameraProcess` (`src/processes/camera.py`) captures frames in-process using ximea-py with `ctypes.memmove` zero-copy into a pre-allocated linear double-buffer. On ZONE_ENTER, recording starts; on ZONE_EXIT (or buffer full), the active buffer is handed to a background encoder thread that pipes raw frames to ffmpeg (NVENC with x264 fallback). Requires `ffmpeg` on PATH and the XIMEA SDK.
