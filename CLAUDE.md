# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OptoFly is a real-time tracking and closed-loop optogenetic stimulation system for flying insects. It integrates multiple hardware components (Braid tracking, Ximea cameras, optogenetic triggers, liquid lenses, multi-screen displays) using ZMQ for inter-process communication.

**Core workflow:** Braid tracks flies at 100fps → Trigger Handler applies spatial/temporal gating → Ximea Camera records triggered videos (500fps) + Opto Trigger activates LED + Visual Stimuli display patterns.

## Build and Test Commands

### Python Development

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run specific test
uv run pytest tests/test_camera_integration.py

# Lint code (if configured)
uv run ruff check .
```

### Rust Camera System

```bash
# Build camera binary
cd rust/ximea_camera
cargo build --release
cd ../..

# Run tests (most require Ximea hardware)
cd rust/ximea_camera
cargo test
cd ../..
```

### Integration Testing

```bash
# Test camera system end-to-end
python tests/test_camera_integration.py

# Test visual stimuli standalone (small window)
python -m src.processes.visual_stimuli --standalone

# Test on experimental display (set use_experimental_display=true in config.toml)
python -m src.processes.visual_stimuli --standalone
```

## Architecture Overview

### Multi-Process Design

OptoFly uses Python `multiprocessing` with a shared `Event` for coordinated shutdown. All processes inherit from `WorkerProcess` base class (`src/utils/worker_process.py`).

**Key processes:**
- `TriggerHandler` (`src/processes/trigger_handler.py`): Subscribes to Braid ZMQ feed, applies spatial/temporal gating, publishes TRIGGER messages
- `CameraProcess` (`src/processes/ximea_camera.py`): Python wrapper for Rust camera binary, manages lifecycle
- `OptoTriggerWorker` (`src/processes/opto_trigger_worker.py`): Controls optogenetic LED via serial
- `LiquidLens` (`src/processes/liquid_lens.py`): Adjusts focus based on fly depth with Kalman filtering
- `VisualStimuliProcess` (`src/processes/visual_stimuli.py`): Renders visual patterns at 240Hz on multi-screen display

### ZMQ Communication

All inter-process communication uses ZeroMQ pub/sub pattern:

**Topics:**
- `BRAID`: Published by BraidPublisher, consumed by TriggerHandler
- `TRIGGER`: Published by TriggerHandler, consumed by Camera/Opto/Lens/Visual processes
- `LENS`: Published by TriggerHandler, consumed by LiquidLens

**Message format:**
```python
# TRIGGER message
{
    "obj_id": 123,
    "frame": 4567,
    "braid_timestamp": 123456.789,
    "trigger_timestamp": 123456.790,
    "mean_heading": 1.57  # radians
}
```

### Configuration System

All configuration lives in `config.toml` (root directory). Parsed by `src/utils/config.py` which provides typed config classes:
- `TriggerHandlerConfig`
- `CameraConfig`
- `OptoTriggerConfig`
- `LiquidLensConfig`
- `VisualStimuliConfig`

**Important:** Use `ConfigBase.load(config_path)` to load and validate configuration. Do not parse TOML manually.

## Visual Stimuli System (Critical Design Pattern)

The visual stimuli system uses a **factory-based plugin architecture** (`src/visual_stimuli/`). This is the MOST important pattern to understand when adding new stimuli.

### Adding a New Stimulus (Factory Pattern)

**You only need to do 3 things:**

1. **Create stimulus class** in `src/visual_stimuli/your_stimulus.py`:
   ```python
   class YourStimulus:
       def __init__(self, config, geometry_utils, logger, csv_writer):
           # Initialize from config

       def on_trigger(self, trigger_data):
           # Handle TRIGGER message (closed-loop only)

       def update(self, dt):
           # Update state each frame (240Hz)

       def render(self, batch):
           # Add/update pyglet shapes

       def is_active(self):
           # Return True if should be rendered

       def cleanup(self):
           # Clean up resources
   ```

2. **Register in factory** (`src/visual_stimuli/stimulus_factory.py`):
   ```python
   from src.visual_stimuli.your_stimulus import YourStimulus

   StimulusFactory.register(
       "your_stimulus",           # Config key
       YourStimulus,               # Class
       requires_geometry=True,     # Needs GeometryUtils?
       requires_window_height=False # Needs window_height param?
   )
   ```

3. **Add config section** in `config.toml`:
   ```toml
   [visual_stimuli.your_stimulus]
   enabled = true
   # ... your parameters ...
   ```

**That's it!** No changes to `visual_stimuli.py` needed. The factory automatically discovers and instantiates your stimulus.

### Visual Stimuli Key Concepts

- **Pyglet Batch**: All shapes added to single `pyglet.graphics.Batch` for efficient rendering. Pass `batch` parameter when creating shapes.
- **Shape Persistence**: Update shape properties in-place (e.g., `circle.radius = 50`) instead of deleting/recreating each frame.
- **Coordinate Systems**: Use `GeometryUtils` to convert fly heading (radians) → screen pixels and angular size (degrees) → pixel radius.
- **Open-loop vs Closed-loop**: Open-loop stimuli (e.g., static pattern) always render. Closed-loop stimuli (e.g., looming) respond to TRIGGER messages.
- **Edge Wrapping**: Display wraps cylindrically. Stimuli extending beyond edges need "wrapped" shapes on opposite side.

See `docs/adding_new_stimuli.md` and `src/visual_stimuli/README.md` for complete documentation.

## Rust Camera System (Ximea)

Location: `rust/ximea_camera/`

**Architecture:** Double-buffer design for safe, race-free 500fps capture:
- **Camera Reader**: Captures frames, manages active/standby buffer swap
- **Buffer Manager**: Listens for TRIGGER messages, counts after-frames
- **Video Writer**: Receives owned buffer, encodes to MP4 via FFmpeg

**Safety-first design:**
- Zero `unsafe` blocks - compiler-verified safety
- Exclusive buffer ownership during encoding (no concurrent access)
- Lock-free channels for coordination

**Key files:**
- `src/main.rs`: Process orchestration
- `src/camera_reader.rs`: Capture loop and buffer swap
- `src/ring_buffer.rs`: Circular buffer with safe `&mut` access
- `src/video_writer.rs`: FFmpeg encoding with NVENC acceleration

**Memory:** ~10GB for default settings (2 buffers × 1250 frames × 2016² pixels). Includes 500ms safety margin to prevent race conditions.

See `rust/ximea_camera/README.md` for detailed documentation.

## Common Development Patterns

### Adding a New Process

1. **Inherit from `WorkerProcess`** (`src/utils/worker_process.py`):
   ```python
   from src.utils.worker_process import WorkerProcess

   class MyProcess(WorkerProcess):
       def __init__(self, config_path, event):
           super().__init__(
               event=event,
               log_level="INFO",
               log_color="CYAN",
               process_name="MyProcess"
           )
           self.config = ConfigBase.load(config_path).my_process

       def run(self):
           self._initialize_logger()
           # ... process logic ...
   ```

2. **Add config section** to `src/utils/config.py`:
   ```python
   @dataclass
   class MyProcessConfig:
       active: bool = True
       # ... fields ...
   ```

3. **Update main config class** in `src/utils/config.py`:
   ```python
   @dataclass
   class ConfigBase:
       my_process: MyProcessConfig
       # ... other processes ...
   ```

### ZMQ Subscriber Pattern

```python
context = zmq.Context()
subscriber = context.socket(zmq.SUB)
subscriber.connect(f"tcp://127.0.0.1:{port}")
subscriber.setsockopt_string(zmq.SUBSCRIBE, "TOPIC")

poller = zmq.Poller()
poller.register(subscriber, zmq.POLLIN)

while not self.event.is_set():
    socks = dict(poller.poll(timeout=100))
    if subscriber in socks:
        topic, message = subscriber.recv_multipart()
        data = json.loads(message.decode('utf-8'))
        # Process data...
```

### ZMQ Publisher Pattern

```python
context = zmq.Context()
publisher = context.socket(zmq.PUB)
publisher.bind(f"tcp://*:{port}")

# Publish message
data = {"obj_id": 123, "frame": 4567}
publisher.send_multipart([
    b"TOPIC",
    json.dumps(data).encode('utf-8')
])
```

### CSV Logging

```python
from src.classes.csv_writer import CSVWriter

csv_writer = CSVWriter(filename="data.csv")
csv_writer.append({
    "timestamp": time.time(),
    "obj_id": 123,
    "parameter": 42
})
```

## Code Style and Conventions

### Python

- **Type hints**: Use type hints for all function signatures
- **Dataclasses**: Use `@dataclass` for data structures (see `trigger_handler.py`)
- **Logging**: Use `loguru` logger initialized via `init_class_logger()` from `src/utils/custom_logger.py`
- **Error handling**: Log errors clearly with context (obj_id, frame numbers)
- **Configuration**: Never hardcode parameters - use `config.toml`

### Rust (Camera System)

- **Safety-first**: Avoid `unsafe` blocks. Use ownership model for safety guarantees.
- **Simple and direct**: Prefer straightforward code over complex async patterns
- **Comments**: Explain "why", not "what"

### Naming Conventions

- **Processes**: Suffix with `Process` or `Worker` (e.g., `CameraProcess`, `OptoTriggerWorker`)
- **Config sections**: Snake_case matching class name (e.g., `trigger_handler`, `visual_stimuli`)
- **ZMQ topics**: ALL_CAPS (e.g., `BRAID`, `TRIGGER`)

## Testing Patterns

### Unit Tests

Place in `tests/` directory. Use pytest:

```python
import pytest
from src.utils.config import ConfigBase

def test_config_loading():
    config = ConfigBase.load("config.toml")
    assert config.trigger_handler.radius > 0
```

### Integration Tests

Test full workflow with simulated ZMQ messages:

```python
import zmq
import json
import time

# Start processes
trigger = TriggerHandler(config_path="config.toml", event=stop_event)
trigger.start()

# Simulate Braid message
context = zmq.Context()
publisher = context.socket(zmq.PUB)
publisher.bind("tcp://*:5555")
time.sleep(0.1)  # Allow connection

braid_data = {"obj_id": 1, "x": 0.0, "y": 0.0, "z": 0.2}
publisher.send_multipart([b"BRAID", json.dumps(braid_data).encode()])
```

## Pre-flight Checks Pattern

For hardware-dependent processes, implement pre-flight checks:

```python
from src.processes.ximea_camera import check_camera_prerequisites

results = check_camera_prerequisites("config.toml")
if not results["overall"]:
    for error in results["errors"]:
        print(f"Error: {error}")
    exit(1)
```

Check for:
- Binary/executable exists
- Hardware accessible (camera, serial port)
- Dependencies installed (FFmpeg, ZMQ)
- Folders writable
- Ports available

## Common Gotchas

1. **Submodule updates**: Camera is a git submodule. After pulling, run:
   ```bash
   git submodule update --init --recursive
   ```

2. **ZMQ topic subscription**: Must match exactly (case-sensitive). Use constants:
   ```python
   TRIGGER_TOPIC = "TRIGGER"
   subscriber.setsockopt_string(zmq.SUBSCRIBE, TRIGGER_TOPIC)
   ```

3. **Multiprocessing Event**: Pass same `Event` instance to all processes for coordinated shutdown:
   ```python
   stop_event = mp.Event()
   # Pass to all processes
   # Later: stop_event.set() to shutdown
   ```

4. **Pyglet batch rendering**: Always pass `batch` parameter when creating shapes, otherwise they won't render:
   ```python
   # WRONG: shape not visible
   circle = pyglet.shapes.Circle(x, y, radius)

   # RIGHT: shape added to batch
   circle = pyglet.shapes.Circle(x, y, radius, batch=batch)
   ```

5. **Camera binary location**: Python wrapper expects binary at `rust/ximea_camera/target/release/ximea_camera`. Rebuild after pulling.

6. **Performance monitoring**: Visual stimuli logs warnings if FPS drops below 222Hz. Check for shape recreation in `render()` methods.

7. **Coordinate systems**: Don't manually calculate pixel positions. Use `GeometryUtils.heading_to_pixel_x()` and `degrees_to_pixels()`.

## Troubleshooting

### Camera not recording
- Check binary exists: `ls rust/ximea_camera/target/release/ximea_camera`
- Run pre-flight checks: `python tests/test_camera_integration.py`
- Check ZMQ connection: `netstat -tulpn | grep 5556`
- Verify camera detected: `lsusb | grep Ximea`

### Visual stimuli not appearing
- Check `enabled = true` in config section
- Verify stimulus registered (look for log message at startup)
- Ensure `batch` parameter passed to shapes
- Check `is_active()` returns True

### ZMQ messages not received
- Verify publisher/subscriber on same port
- Check topic subscription matches exactly
- Add small delay after bind before sending (`time.sleep(0.1)`)
- Use `zmq.Poller` with timeout to avoid blocking

### Performance issues (visual stimuli < 240Hz)
- Check if recreating shapes instead of updating
- Reduce number of shapes (>1000 may slow down)
- Pre-calculate constants in `__init__()` not `update()`
- Use simpler shapes (rectangles faster than circles)

## Key Files Reference

**Configuration:**
- `config.toml`: Main configuration file (root)
- `src/utils/config.py`: Config parsing and validation

**Processes:**
- `src/processes/trigger_handler.py`: Spatial/temporal gating logic
- `src/processes/ximea_camera.py`: Python wrapper for camera binary
- `src/processes/opto_trigger_worker.py`: LED control via Arduino
- `src/processes/visual_stimuli.py`: Main rendering loop (240Hz)
- `src/processes/liquid_lens.py`: Focus adjustment with Kalman filter

**Visual Stimuli:**
- `src/visual_stimuli/stimulus_factory.py`: **Start here** for new stimuli
- `src/visual_stimuli/geometry_utils.py`: Coordinate conversions
- `src/visual_stimuli/looming_stimulus.py`: Example closed-loop stimulus
- `src/visual_stimuli/static_pattern.py`: Example open-loop stimulus

**Utilities:**
- `src/utils/worker_process.py`: Base class for all processes
- `src/utils/custom_logger.py`: Logging setup
- `src/classes/csv_writer.py`: CSV logging for experiments

**Documentation:**
- `docs/adding_new_stimuli.md`: Step-by-step stimulus creation
- `src/visual_stimuli/README.md`: Complete visual stimuli guide
- `rust/ximea_camera/README.md`: Camera architecture and usage

**Tests:**
- `tests/test_camera_integration.py`: End-to-end camera test
