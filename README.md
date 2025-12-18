# OptoFly

Real-time tracking and closed-loop optogenetic stimulation system for flying insects.

## System Overview

OptoFly integrates multiple hardware and software components for automated closed-loop behavioral experiments:

- **Braid Tracking**: Real-time 3D fly tracking at 100fps
- **Trigger Handler**: Spatial/temporal gating with heading detection
- **Ximea Camera**: High-speed (500fps) triggered video recording
- **Optogenetic Trigger**: LED stimulation control via Arduino
- **Liquid Lens**: Dynamic autofocus with optional predictive tracking
- **Visual Stimuli**: Configurable patterns at 240Hz (looming, gratings, static)

---

## Quick Start

### Prerequisites

**Python environment (choose one):**

Option 1: **uv** (recommended - faster)
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

Option 2: **conda/mamba**
```bash
# Create environment from file
mamba env create -f environment.yml  # or use 'conda'
conda activate optofly
```

**Rust toolchain** (for camera binary)
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

**System dependencies** (Ubuntu/Debian)
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

The camera binary will be at: `rust/ximea_camera/target/release/ximea_camera`

### Configuration

OptoFly uses example configuration files as templates. **Copy and customize them:**

```bash
# Copy example configs to create your local configs
cp config.example.toml config.toml
cp visual_stimuli.example.toml visual_stimuli.toml

# Edit with your settings
nano config.toml
nano visual_stimuli.toml
```

**Important:** Your local `config.toml` and `visual_stimuli.toml` are git-ignored, so you can customize them without affecting version control.

#### Key Configuration Sections

**`config.toml`** - System and hardware settings:

```toml
[braid_publisher]
url = "http://10.40.80.6:8397"          # Braid server address
experiments_path = "/mnt/data/experiments/"  # Where .braid folders are saved

[trigger_handler]
min_trajectory_time = 1.0   # Min tracking duration before triggering (s)
min_trigger_interval = 10.0 # Cooldown between triggers (s)
radius = 0.05               # Trigger zone radius (m)
z_lim = [0.15, 0.25]        # Vertical trigger zone limits (m)
heading_cone_deg = 30.0     # Heading threshold (degrees from center-directed)
min_velocity = 0.01         # Min velocity to consider as "moving" (m/s)

[camera]
active = true               # Enable/disable camera recording
resolution = [2016, 2016]
fps = 500
pre_trigger_time = 0.5      # Seconds of pre-trigger buffer
post_trigger_time = 1.5     # Seconds to record after trigger

[opto_trigger]
active = true               # Enable/disable optogenetic LED
port = "/dev/opto_trigger"
duration = [100, 200, 300]  # Pulse duration in ms (random selection)
intensity = [0, 51, 102, 153, 204, 255]  # LED brightness (random)
color = "red"

[liquid_lens]
active = false              # Enable/disable autofocus
port = "/dev/ttyUSB1"

[liquid_lens.prediction]    # Predictive lens tracking (experimental)
enabled = false             # Enable trajectory-based early tracking
horizon = 1.5               # Look-ahead time (seconds)

[visual_stimuli]
active = true               # Enable/disable visual display
config_file = "visual_stimuli.toml"
```

**`visual_stimuli.toml`** - Stimulus parameters:

```toml
[display]
window_width = 7680         # Total display width (4×1920)
window_height = 1080
arena_center_to_screen_cm = 25.0

[visual_stimuli.static]
enabled = true
pattern_density = 0.3       # Fraction of black pixels (0.0-1.0)
random_seed = 42            # For reproducible patterns

[visual_stimuli.looming]
enabled = true
initial_size_deg = [5.0, 10.0, 15.0]  # Randomized on each trigger
final_size_deg = 72.0
expansion_duration_ms = [300, 500, 700]  # Randomized
positions_deg = [-90, 0, 90]  # Left, front, right (balanced)
```

See the example files for complete configuration options with detailed comments.

### Run Experiment

**Step 1:** Start Braid recording FIRST (creates the `.braid` folder for data logging)

**Step 2:** Launch OptoFly

```bash
# With uv
uv run python main.py

# Or with conda
python main.py
```

The launcher will:
1. ✅ Check for today's Braid recording folder
2. ✅ Load configuration from `config.toml` and `visual_stimuli.toml`
3. ✅ Start enabled processes (Braid subscriber, trigger handler, camera, LED, lens, visual stimuli)
4. ✅ Display experiment summary
5. ✅ Run until Ctrl+C

**Example output:**
```
Loading configuration from config.toml...
Checking for braid folder with date 20251218 in /mnt/data/experiments/...
✓ Found braid folder: /mnt/data/experiments/20251218_143022.braid

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

Trigger Settings:
  Zone: radius=0.05m, z=[0.15, 0.25]m
  Heading: ±30° from center-directed
  Timing: 1.0s min trajectory, 10.0s cooldown

Visual Stimuli:
  ✓ Static pattern
  ✓ Looming stimulus

Opto Trigger:
  Color: red
  Intensity: [0, 51, 102, 153, 204, 255] (random)
  Duration: [100, 200, 300] ms (random)

Press Ctrl+C to stop the experiment
======================================================================
```

---

## System Architecture

### Data Flow

```
Braid Server (100fps 3D tracking)
    ↓ ZMQ pub/sub
    ↓ Topic: BRAID
    ↓
┌───▼────────────────────────┐
│   Trigger Handler          │  Evaluates tracking data:
│   (tracking.py)            │  • Spatial: in cylinder zone?
│                            │  • Heading: toward center?
│                            │  • Temporal: tracked >1s? cooldown >10s?
└───┬────────────────────────┘
    ↓ ZMQ pub/sub
    ↓ Topic: TRIGGER
    ↓
    ├──► Ximea Camera (records 500fps video)
    ├──► Opto Trigger (activates LED)
    ├──► Visual Stimuli (displays patterns)
    └──► Liquid Lens (adjusts focus)
```

### Process Architecture

All processes inherit from `WorkerProcess` and run independently:

| Process | Purpose | ZMQ Role |
|---------|---------|----------|
| **BraidPublisher** | Connects to Braid, forwards tracking data | Publisher (BRAID) |
| **TriggerHandler** | Evaluates triggers from tracking data | Subscriber (BRAID), Publisher (TRIGGER) |
| **CameraProcess** | Manages Rust camera binary, saves videos | Subscriber (TRIGGER) |
| **OptoTriggerWorker** | Controls LED via serial, logs stimulations | Subscriber (TRIGGER) |
| **VisualStimuliProcess** | Renders visual patterns at 240Hz | Subscriber (TRIGGER) |
| **LiquidLens** | Adjusts autofocus based on fly position | Subscriber (TRIGGER/LENS) |

---

## Components

### Trigger Handler (`src/processes/tracking.py`)

The brain of the closed-loop system. Processes Braid tracking data and generates trigger signals.

**Trigger Conditions (ALL must be met):**
1. **Spatial**: Fly within cylindrical trigger zone (radius + z-limits)
2. **Heading**: Fly moving toward arena center (within configurable cone)
3. **Temporal**: Tracked continuously for ≥ 1.0s
4. **Cooldown**: ≥10s since last trigger (prevents excessive stimulation)
5. **Velocity**: Fly moving (velocity > 0.01 m/s)

**Heading Detection:**
- Uses circular mean of last 10 velocity vectors for stable heading estimate
- Compares mean heading vs. angle-to-center
- Configurable cone threshold (default: ±30°)

**ZMQ Protocol:**
- Subscribes to: `BRAID` topic (tracking updates from Braid)
- Publishes: `TRIGGER` topic (obj_id, frame, timestamp, heading)

**Predictive Lens Tracking** (experimental, on `feature/predictive-lens-tracking` branch):
- Predicts if fly trajectory will intersect trigger zone
- Sends early `LENS` trigger to liquid lens before actual trigger
- Uses linear extrapolation with configurable horizon (default: 1.5s)
- Enable with `[liquid_lens.prediction] enabled = true`

### Ximea Camera (`rust/ximea_camera/`)

High-speed video recording system written in Rust for performance and safety.

**Specifications:**
- **Frame rate**: 500fps @ 2016×2016 pixels
- **Encoding**: H.264 with NVENC hardware acceleration
- **Output**: MP4 videos + CSV metadata
- **Buffer**: Auto-sized ring buffer (~4GB for 1000 frames)

**Architecture:**
- **Camera Reader**: Captures frames in tight loop
- **Buffer Manager**: Handles TRIGGER messages, manages buffer swaps
- **Video Writer**: Encodes and saves videos (non-blocking)

**Safety:**
- Zero `unsafe` blocks
- Ownership-based concurrency (no race conditions)
- Compiler-verified memory safety

See [`rust/ximea_camera/README.md`](rust/ximea_camera/README.md) for detailed documentation.

**Python Wrapper:**
```python
from src.processes.ximea_camera import CameraProcess, check_camera_prerequisites

# Pre-flight checks
results = check_camera_prerequisites("config.toml")
if not results["overall"]:
    for error in results["errors"]:
        print(f"Error: {error}")

# Start camera
camera = CameraProcess(config_path="config.toml", event=stop_event)
camera.start()
```

### Optogenetic Trigger (`src/processes/led.py`)

Controls LED stimulation via Arduino serial interface.

**Features:**
- Randomized parameters (duration, intensity, frequency) for variability
- Sham trial support (configurable probability)
- CSV logging of all stimulation events
- Validates trigger messages before activation

**Hardware:**
- Arduino with custom firmware
- Serial protocol: `<color><intensity><duration><frequency>`

**CSV Output:**
Logs to `opto.csv` in Braid folder with columns:
- obj_id, frame, braid_timestamp, trigger_timestamp
- mean_heading, duration, intensity, frequency, color, sham

### Liquid Lens (`src/processes/lens.py`)

Dynamic autofocus system using Optotune liquid lens.

**Features:**
- Real-time focus adjustment based on fly Z-position
- Kalman filter for predictive focus (compensates for latency)
- Calibration-based diopter calculation
- Tracking timeout protection

**Modes:**
1. **Standard**: Responds to TRIGGER messages, tracks flies in camera FOV
2. **Predictive** (experimental): Receives LENS messages before trigger, starts tracking flies predicted to enter trigger zone

**Configuration:**
```toml
[liquid_lens]
active = true
port = "/dev/ttyUSB1"
calibration_file = "calibrations/liquid_lens.csv"

[liquid_lens.kalman]
enabled = true
prediction_horizon = 0.1  # Kalman prediction for focus lag compensation

[liquid_lens.prediction]
enabled = false           # Trajectory-based predictive tracking
horizon = 1.5             # Start tracking 1.5s before predicted trigger
```

### Visual Stimuli (`src/stimuli/`)

High-performance visual stimulus rendering at 240Hz using Pyglet.

**Architecture:**
- **Plugin-based**: Extensible stimulus registry (factory pattern)
- **Batch rendering**: All shapes in single `pyglet.graphics.Batch` for GPU efficiency
- **Closed-loop**: Responds to TRIGGER messages with fly heading data
- **Geometry utilities**: Automatic conversion (fly heading → screen pixels)

**Display Setup:**
- 7680×1080 total resolution (4× 1920×1080 screens)
- 240Hz refresh rate with VSync
- Cylindrical projection around arena
- White background

**Available Stimuli:**

1. **Static Pattern** (`static.py`)
   - QR-code-like random binary pattern
   - Open-loop (always displayed)
   - Reproducible via random seed
   - Optimized: single sprite vs. hundreds of rectangles

2. **Looming Stimulus** (`looming.py`)
   - Expanding circle simulating approaching threat
   - Closed-loop (triggered by fly tracking)
   - L/V ratio or exponential expansion
   - Position balancing (left/front/right)
   - Randomizable parameters
   - Edge wrapping for cylindrical display

3. **Vertical Bar** (`vertical_bar.py`)
   - Moving vertical bar stimulus
   - Configurable width, speed, color

**Performance:**
- Targets 240Hz (4.17ms frame time)
- Logs warnings if FPS drops below 222Hz
- CSV logging of all stimulus presentations

**Adding Custom Stimuli:**

See [`src/stimuli/README.md`](src/stimuli/README.md) for the factory pattern guide.

Quick example:
```python
# 1. Create stimulus class in src/stimuli/my_stimulus.py
class MyStimulus:
    def __init__(self, config, geometry_utils, logger, csv_writer):
        pass
    def on_trigger(self, trigger_data):
        pass
    def update(self, dt):
        pass
    def render(self, batch):
        pass
    def is_active(self):
        return True

# 2. Register in src/stimuli/stimulus_factory.py
StimulusFactory.register("my_stimulus", MyStimulus, requires_geometry=True)

# 3. Add config in visual_stimuli.toml
[visual_stimuli.my_stimulus]
enabled = true
# ... parameters ...
```

---

## Development

### Project Structure

```
OptoFly/
├── config.example.toml           # System config template
├── visual_stimuli.example.toml   # Stimuli config template
├── config.toml                   # Your local config (git-ignored)
├── visual_stimuli.toml           # Your local stimuli config (git-ignored)
├── environment.yml               # Conda environment
├── pyproject.toml                # Python dependencies (uv)
├── main.py                       # Experiment launcher
├── src/
│   ├── hardware/                 # Hardware device controllers
│   │   ├── led.py                # Optogenetic LED (Arduino)
│   │   └── lens.py               # Liquid lens (Optotune)
│   ├── processes/                # Multi-process workers
│   │   ├── braid.py              # Braid tracking subscriber
│   │   ├── tracking.py           # Trigger handler (core logic)
│   │   ├── camera.py             # Camera process wrapper
│   │   ├── led.py                # LED process wrapper
│   │   ├── lens.py               # Lens process wrapper
│   │   └── visual.py             # Visual stimuli renderer
│   ├── stimuli/                  # Visual stimulus generators
│   │   ├── base.py               # Base stimulus interface
│   │   ├── registry.py           # Stimulus factory
│   │   ├── static.py             # Static pattern
│   │   ├── looming.py            # Looming circles
│   │   └── geometry_utils.py    # Coordinate conversions
│   ├── utils/                    # Shared utilities
│   │   ├── config.py             # Configuration loading
│   │   ├── worker.py             # Base WorkerProcess class
│   │   ├── logger.py             # Logging setup
│   │   └── csv_writer.py         # CSV data logging
│   └── tools/                    # Development tools
│       ├── braid_simulator.py    # Simulate Braid messages
│       └── braid_visualizer.py   # Visualize tracking data
├── rust/
│   └── ximea_camera/             # High-speed camera (Rust)
└── tests/                        # Integration tests
```

### Development Workflow

**Branching Strategy:**

The project uses feature branches and git worktrees for parallel development:

```bash
# List current branches and worktrees
git branch -a
git worktree list

# Create feature branch
git checkout -b feature/my-feature

# Or use worktree for parallel work
git worktree add .worktrees/my-feature -b feature/my-feature
cd .worktrees/my-feature
# ... make changes ...
git commit -m "feat: add my feature"
```

**Active Development Branches:**
- `main` - Stable production code
- `feature/predictive-lens-tracking` - Predictive lens tracking (ready for testing)
- `remote_monitoring` - Remote monitoring server (merged to main)

**Code Style:**
- Python: PEP 8, type hints, docstrings
- Rust: `cargo fmt`, avoid complex async patterns
- Comments: Explain "why", not "what"

**Testing:**

```bash
# Python unit tests
uv run pytest

# Rust tests
cd rust/ximea_camera
cargo test

# Integration test (camera system)
python tests/test_camera_integration.py

# Visual stimuli standalone test
python -m src.processes.visual_stimuli --standalone
```

### Adding New Features

**For new processes:**
1. Inherit from `WorkerProcess` (`src/utils/worker.py`)
2. Add configuration to `src/utils/config.py`
3. Update `config.toml` example
4. Use ZMQ for inter-process communication
5. Add integration tests

**For new visual stimuli:**
1. Follow factory pattern (see `src/stimuli/README.md`)
2. Register in `StimulusFactory`
3. Add config section to `visual_stimuli.toml`
4. Test standalone before integration

---

## Testing

### Unit Tests

```bash
# All Python tests
uv run pytest

# Specific test
uv run pytest tests/test_config.py -v

# Rust tests (require Ximea hardware)
cd rust/ximea_camera
cargo test
```

### Integration Tests

```bash
# Camera system end-to-end
python tests/test_camera_integration.py
```

Verifies:
- Pre-flight checks pass
- Camera process starts
- Trigger messages received
- Video files created

### Visual Stimuli Testing

```bash
# Standalone test (small window, no hardware)
python -m src.processes.visual_stimuli --standalone

# Test on experimental display (if configured)
# Edit visual_stimuli.toml: use_experimental_display = true
python -m src.processes.visual_stimuli --standalone
```

### End-to-End Experiment Test

1. Start Braid server with camera feed
2. Run OptoFly with all components enabled
3. Introduce fly (or laser pointer) into arena
4. Verify:
   - ✅ Tracking data appears in trigger_handler logs
   - ✅ Triggers generate when fly enters trigger zone
   - ✅ Videos save to `camera_videos/`
   - ✅ Opto stimulation fires (if hardware connected)
   - ✅ Visual stimuli display on screens
   - ✅ Data logged to CSV files in `.braid` folder

---

## Troubleshooting

### Setup Issues

**Camera binary not found**
```bash
# Build the camera binary
cd rust/ximea_camera
cargo build --release
ls target/release/ximea_camera  # Verify it exists
cd ../..
```

**ZMQ connection refused**
```bash
# Check Braid server is running and publishing
netstat -tulpn | grep 5555

# If not found, verify Braid server config
# In Braid: Check ZMQ port settings
```

**Camera permission denied**
```bash
# Add user to video group
sudo usermod -a -G video $USER

# Log out and back in for changes to take effect
groups  # Verify 'video' appears
```

**Config files missing**
```bash
# Copy example files
cp config.example.toml config.toml
cp visual_stimuli.example.toml visual_stimuli.toml

# Then edit with your settings
```

### Runtime Issues

**No triggers generated**

Check trigger handler logs:
- Are flies being tracked? (should see "Update" messages)
- Are flies heading toward center? (check heading values)
- Are flies in trigger zone? (check position vs. radius/z_lim)
- Is cooldown expired? (check time since last trigger)

Enable debug logging:
```toml
[logging]
level = "DEBUG"
```

**Visual stimuli not appearing**

- Check `enabled = true` in config
- Verify display connected and detected
- Test standalone: `python -m src.processes.visual_stimuli --standalone`
- Check window appears (may be on different screen)

**Camera not recording**

Pre-flight checks:
```python
from src.processes.ximea_camera import check_camera_prerequisites
results = check_camera_prerequisites("config.toml")
print(results)
```

Common issues:
- Camera not detected: `lsusb | grep Ximea`
- Binary not found: Rebuild with `cargo build --release`
- Permissions: Add user to `video` group
- Disk space: Check output folder has space

**Video encoding slow**

Install NVIDIA drivers for NVENC hardware acceleration:
```bash
sudo ubuntu-drivers autoinstall
# Reboot
ffmpeg -encoders | grep nvenc  # Should show nvenc_h264
```

**Liquid lens not responding**

- Check serial port: `ls -l /dev/ttyUSB*`
- Verify permissions: `sudo chmod 666 /dev/ttyUSB1`
- Check calibration file exists: `ls calibrations/liquid_lens.csv`
- Test connection: See lens documentation

### Performance Issues

**Visual stimuli FPS drops**

- Check for shape recreation (should update in-place)
- Reduce number of shapes (>1000 may slow down)
- Pre-calculate constants in `__init__()` not `update()`
- Use simpler shapes (rectangles faster than circles)

**Camera frame drops**

- Check CPU usage during recording
- Verify NVENC is being used (check ffmpeg output)
- Reduce resolution or frame rate if needed
- Ensure sufficient disk I/O bandwidth

---

## Citation

If you use OptoFly in your research, please cite:

```bibtex
@software{optofly2024,
  title = {OptoFly: Real-time Closed-Loop Optogenetic Stimulation System},
  author = {Your Name},
  year = {2024},
  url = {https://github.com/yourusername/OptoFly}
}
```

## License

[Add your license here]

## Contact

[Add contact information]

---

## Acknowledgments

Built with:
- [Braid](https://github.com/strawlab/strand-braid) - Multi-camera 3D tracking
- [Pyglet](https://pyglet.org/) - OpenGL rendering
- [ZeroMQ](https://zeromq.org/) - Inter-process messaging
- [Ximea](https://www.ximea.com/) - High-speed cameras
