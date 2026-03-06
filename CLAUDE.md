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
uv run pytest tests/test_config.py -v   # single file

# Lint
uv run ruff check .
uv run ruff format .

# Build Rust camera binary
cd rust/ximea_camera && cargo build --release
# Binary: rust/ximea_camera/target/release/ximea_camera

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
TriggerHandler  →  ZMQ PUB  topic=TRIGGER  port=5556
    ↓
    ├── CameraProcess       (records 500fps video via Rust binary)
    ├── OptoTriggerWorker   (fires LED via Arduino serial)
    ├── VisualStimuliProcess (renders stimuli at 240Hz via pyglet)
    └── LiquidLens          (adjusts Optotune focus via serial)
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

**TRIGGER** (from TriggerHandler):
```json
{"obj_id": 1, "frame": 12345, "timestamp": 1234.56, "heading": 0.52}
```

### Configuration Loading

`src/utils/config.py` has typed config classes (e.g. `LiquidLensConfig`, `ZMQConfig`) that load from TOML sections. Pass `config_path` to each process; don't read TOML directly elsewhere.

### Visual Stimuli

Plugin-based factory pattern in `src/stimuli/`. To add a stimulus:
1. Create class in `src/stimuli/my_stimulus.py` implementing `on_trigger`, `update`, `render`, `is_active`
2. Register in `src/stimuli/registry.py`
3. Add `[visual_stimuli.my_stimulus]` section to `configs/visual_stimuli.toml`

Heading-to-pixel conversion uses `GeometryUtils` (`src/stimuli/geometry.py`). With `use_empirical_calibration = true`, it interpolates from `calibrations/heading_mapping_model.npz`; otherwise it uses a geometric formula.

### Liquid Lens Calibration

`calibrations/liquid_lens.csv` maps `z` (meters) → `dpt` (diopters). Create it manually by stepping through z-heights and finding the in-focus diopter value with `src/hardware/lens.py:LensDriver`. See `calibrations/LIQUID_LENS_CALIBRATION.md`.

### Rust Camera

Three concurrent threads coordinated via crossbeam channels: Camera Reader → Buffer Manager → Video Writer. Uses double-buffer ownership transfer (no `unsafe`). See `rust/ximea_camera/CLAUDE.md` for full details.
