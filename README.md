# OptoFly

Real-time tracking and closed-loop optogenetic stimulation system for flying insects.

## System Overview

OptoFly integrates multiple components for automated behavioral experiments:

- **Braid Tracking**: Real-time 3D fly tracking at 100fps
- **Trigger Handler**: Spatial/temporal gating for stimulation events
- **Ximea Camera**: High-speed (500fps) triggered video recording
- **Opto Trigger**: Optogenetic stimulation control
- **Liquid Lens**: Dynamic focus adjustment
- **Visual Stimuli**: Configurable visual patterns (looming, gratings, etc.)

## Quick Start

### Prerequisites

**Option 1: uv (recommended - faster)**
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

**Option 2: conda/mamba**
```bash
# Create environment
mamba env create -f environment.yml  # or 'conda' instead of 'mamba'
conda activate optofly
```

**Rust toolchain (for camera)**
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

**System dependencies**
```bash
sudo apt-get install -y \
    build-essential \
    libclang-dev \
    libzmq3-dev \
    ffmpeg \
    libavutil-dev \
    libavformat-dev \
    libavfilter-dev \
    libavcodec-dev \
    libswscale-dev
```

### Build Camera System

```bash
cd rust/ximea_camera
cargo build --release
cd ../..
```

### Configuration

OptoFly uses two configuration files:

- **`config.toml`**: System configuration (hardware, tracking, processes)
- **`visual_stimuli.toml`**: Visual stimuli settings (separate for easy experiment presets)

Edit `config.toml` to enable/disable components:

```toml
[braid_publisher]
url = "http://10.40.80.6:8397"
experiments_path = "/mnt/data/experiments/"  # Where Braid saves .braid folders

[trigger_handler]
min_trajectory_time = 1.0   # Minimum tracking duration (seconds)
min_trigger_interval = 10.0 # Cooldown between triggers (seconds)
radius = 0.025              # Trigger zone radius (meters)
z_lim = [0.15, 0.25]       # Vertical trigger zone (meters)
heading_cone_deg = 45.0     # Heading threshold (degrees)
min_velocity = 0.01         # Min velocity to consider as "moving" (m/s)

[camera]
active = true               # Enable/disable camera
resolution = [2016, 2016]
fps = 500
pre_trigger_time = 0.5
post_trigger_time = 1.5

[opto_trigger]
active = true               # Enable/disable LED
port = "/dev/trig1"
color = "red"
intensity = 255             # 0-255
duration = 300              # milliseconds

[liquid_lens]
active = false              # Enable/disable liquid lens

[visual_stimuli]
active = true               # Enable/disable visual stimuli
config_file = "visual_stimuli.toml"  # Path to stimuli config
```

Edit `visual_stimuli.toml` for stimulus parameters (see file for full options).

### Run Experiment

**Important:** Start Braid recording BEFORE running OptoFly (creates the `.braid` folder for data logging).

```bash
# Simple: run with uv
uv run python main.py

# Or with conda environment
python main.py
```

The launcher will:
1. Check for today's Braid recording folder
2. Load configuration from `config.toml` and `visual_stimuli.toml`
3. Start enabled processes (BraidPublisher, TriggerHandler, Camera, LED, Lens, Visual)
4. Display experiment summary
5. Run until Ctrl+C

**Example output:**
```
Loading configuration from config.toml...
Checking for braid folder with date 20251121 in /mnt/data/experiments/...
✓ Found braid folder: /mnt/data/experiments/20251121_143022.braid

Starting core processes...
  - BraidPublisher
  - TriggerHandler

Starting optional processes (based on config)...
  - VisualStimuliProcess
  - CameraProcess
  - OptoTriggerWorker
  - LiquidLens (disabled in config)

======================================================================
OptoFly Experiment Configuration
======================================================================

Active Processes:
  ✓ BraidPublisher
  ✓ TriggerHandler
  ✓ VisualStimuliProcess
  ✓ CameraProcess
  ✓ OptoTriggerWorker

Visual Stimuli:
  ✓ Static pattern

Opto Trigger:
  Color: red
  Intensity: 255
  Duration: 300 ms

Press Ctrl+C to stop the experiment
======================================================================
```

## Components

### Trigger Handler (`src/processes/tracking.py`)

Processes Braid tracking data and generates trigger signals based on:
- Spatial criteria (cylindrical trigger zone)
- Temporal gating (minimum tracking duration, cooldown period)
- Heading direction (flies moving toward center)

**ZMQ Protocol:**
- Subscribes to: `BRAID` messages from Braid server
- Publishes: `TRIGGER` messages with obj_id and frame number

### Ximea Camera (`rust/ximea_camera/`)

High-speed video recording triggered by the trigger_handler:

- **Performance**: 500fps @ 2016�2016, sustained without drops
- **Ring Buffer**: Auto-sized to timing requirements (~4GB for 1000 frames)
- **Output**: H264 MP4 videos + CSV metadata
- **Architecture**: Three-process design (Camera Reader, Buffer Manager, Video Writer)

See [`rust/ximea_camera/README.md`](rust/ximea_camera/README.md) for detailed documentation.

**Python Wrapper:**
```python
from src.processes.ximea_camera import CameraProcess, check_camera_prerequisites

# Run pre-flight checks
results = check_camera_prerequisites("config.toml")
if not results["overall"]:
    for error in results["errors"]:
        print(f"Error: {error}")

# Start camera
camera = CameraProcess(config_path="config.toml")
if camera.initialize():
    camera.start()
```

### Opto Trigger (`src/processes/led.py`)

Controls optogenetic stimulation hardware in response to TRIGGER messages.

### Liquid Lens (`src/processes/lens.py`)

Dynamic focus adjustment to track flies at different depths.

### Visual Stimuli (`src/stimuli/`)

High-performance visual stimulus rendering system running at 240Hz on multi-screen displays.

**Architecture:**
- **Plugin-based design**: Extensible stimulus registry for easy addition of new stimulus types
- **Pyglet rendering**: Hardware-accelerated batched rendering for optimal performance
- **Closed-loop integration**: Responds to TRIGGER messages from trigger_handler
- **Coordinate system**: Automatic conversion between fly heading (radians) and screen pixels

**Display Configuration:**
- 7680×1080 total resolution (four 1920×1080 screens arranged horizontally)
- 240Hz refresh rate with VSync
- Cylindrical projection around experimental arena
- White background with configurable stimuli

**Available Stimulus Types:**

1. **Static Pattern** (`StaticPatternStimulus`)
   - Random QR-code-like background pattern generated from numpy binary matrix
   - Open-loop (always displayed)
   - Single sprite with configurable density and resolution
   - Reproducible patterns via random seed
   - 1 draw call vs old 500 rectangles for optimal performance

2. **Looming Stimulus** (`LoomingStimulusRenderer`)
   - Expanding circle simulating approaching threat
   - Closed-loop (triggered by fly tracking)
   - L/V ratio or exponential expansion dynamics
   - Position balancing across configured angles
   - Supports randomized parameters (size, duration, timing)
   - Edge wrapping for seamless cylindrical display

**Key Features:**
- **Batch rendering**: All shapes added to single pyglet.graphics.Batch for efficient GPU usage
- **Shape persistence**: Circles/rectangles updated in-place rather than recreated each frame
- **CSV logging**: Complete stimulus parameters logged for each presentation
- **Performance monitoring**: Automatic warnings if frame rate drops below 222Hz
- **Geometry utilities**: Built-in heading→pixel and degrees→pixel conversion

**Data Flow:**
```
TRIGGER message from trigger_handler
    ↓
StimulusRegistry.on_trigger()
    ↓
LoomingStimulus selects position and parameters
    ↓
Shape created/updated in pyglet.graphics.Batch
    ↓
batch.draw() at 240Hz renders all active stimuli
```

**Configuration Example:**
```toml
[visual_stimuli]
active = true
window_width = 7680
window_height = 1080
arena_center_to_screen_cm = 25.0

[visual_stimuli.static]
enabled = true
square_color = "black"
background_color = "white"
pattern_density = 0.3        # Probability of pattern pixels (0.0-1.0)
downscale_factor = 2          # 1=full res, 2=half, 4=quarter
random_seed = 42              # Optional: reproducible patterns

[visual_stimuli.looming]
enabled = true
initial_size_deg = [5.0, 10.0, 15.0]  # Randomized
final_size_deg = 80.0
expansion_duration_ms = [300, 500, 700]  # Randomized
hold_time_ms = 200
positions_deg = [-90, 0, 90]  # Balanced presentation
```

See [`src/stimuli/README.md`](src/stimuli/README.md) for detailed documentation on creating custom stimuli.

## Data Flow

```
Braid Server (100fps tracking)
    ↓
ZMQ: BRAID
    ↓
Trigger Handler (spatial/temporal gating)
    ↓
ZMQ: TRIGGER
    ↳ Ximea Camera (records video)
    ↳ Opto Trigger (activates LED)
    ↳ Liquid Lens (adjusts focus)
```

## Testing

### Unit Tests

```bash
# Python tests
uv run pytest

# Rust tests
cd rust/ximea_camera
cargo test
```

### Integration Test

```bash
# Test camera system integration
python tests/test_camera_integration.py
```

This verifies:
- Pre-flight checks pass
- Camera process starts successfully
- Trigger messages are received and processed
- Video files are created

### End-to-End Test

1. Start Braid server with camera feed
2. Run OptoFly with all components enabled
3. Introduce fly into arena
4. Verify:
   - Tracking data appears in trigger_handler logs
   - Triggers are generated when fly enters trigger zone
   - Videos are saved to `camera_videos/`
   - Opto stimulation fires (if hardware connected)

## Configuration Reference

### Camera Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `resolution` | [int, int] | [2016, 2016] | Frame dimensions |
| `fps` | float | 500 | Capture framerate |
| `exposure_time` | float | 2000 | Max exposure (�s) |
| `pre_trigger_time` | float | 0.5 | Seconds before trigger |
| `post_trigger_time` | float | 1.5 | Seconds after trigger |
| `save_folder` | string | "camera_videos" | Output directory |
| `serial` | int | 0 | Camera serial (0=first) |

### Trigger Handler Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `radius` | float | 0.025 | Trigger zone radius (m) |
| `z_lim` | [float, float] | [0, 0] | Z-axis limits (m) |
| `min_trajectory_time` | float | 1.0 | Min tracking duration (s) |
| `min_trigger_interval` | float | 10.0 | Cooldown period (s) |
| `heading_cone_deg` | float | 45.0 | Heading threshold (�) |

## Troubleshooting

### Camera binary not found

```bash
cd rust/ximea_camera
cargo build --release
```

### ZMQ connection refused

Check that Braid server is running and publishing on the expected port:

```bash
netstat -tulpn | grep 5555
```

### Camera permission denied

Add user to `video` group:

```bash
sudo usermod -a -G video $USER
# Log out and back in
```

### Video encoding slow

Install NVIDIA drivers for NVENC hardware acceleration:

```bash
sudo ubuntu-drivers autoinstall
ffmpeg -encoders | grep nvenc  # Verify
```

## Development

### Project Structure

```
OptoFly/
    config.toml             # System configuration
    visual_stimuli.toml     # Visual stimuli configuration
    environment.yml         # Conda/mamba environment
    main.py                 # Experiment launcher
    src/
        hardware/           # Hardware device controllers
            led.py          # Optogenetic LED (Arduino)
            lens.py         # Liquid lens (Optotune)
        processes/          # Multi-process workers
            braid.py        # Braid tracking publisher
            tracking.py     # Trigger handler
            camera.py       # Camera process wrapper
            led.py          # LED process wrapper
            lens.py         # Lens process wrapper
            visual.py       # Visual stimuli process
        stimuli/            # Visual stimulus generators
            static.py       # Static pattern
            looming.py      # Looming circles
            vertical_bar.py # Vertical bar
            ...
        utils/              # Shared utilities
            config.py       # Configuration loading
            worker.py       # Base process class
            logger.py       # Logging utilities
            csv_writer.py   # CSV data logging
        tools/              # Development tools
            braid_simulator.py
            braid_visualizer.py
    rust/
        ximea_camera/       # High-speed camera (Rust)
    tests/                  # Integration tests
```

### Adding New Features

1. Follow the `WorkerProcess` pattern for new processes
2. Use `config.toml` for configuration (add to `src/utils/config.py`)
3. Use ZMQ for inter-process communication
4. Add integration tests

### Code Style

- Python: Follow PEP 8, use type hints
- Rust: Simple, direct code (avoid complex async patterns)
- Comments: Explain "why", not "what"