# Setup

## Prerequisites

**Python environment (choose one):**

Option 1: uv (recommended)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

Option 2: conda/mamba
```bash
mamba env create -f environment.yml
conda activate optofly
```

**System dependencies** (Ubuntu/Debian)
```bash
sudo apt-get install -y \
    build-essential \
    libzmq3-dev \
    ffmpeg
```

**XIMEA SDK** (for camera support)

Install from [ximea.com/support/wiki/apis/XIMEA_Linux_Software_Package](https://www.ximea.com/support/wiki/apis/XIMEA_Linux_Software_Package).

## Configuration

Copy the example configs and customize them:

```bash
cp configs/config.example.toml configs/config.toml
cp configs/visual_stimuli.example.toml configs/visual_stimuli.toml
```

Both files are git-ignored so local customizations are safe.

### configs/config.toml

Key sections:

```toml
[braid_publisher]
host = "127.0.0.1"                          # Braid server IP
events_port = 8397                          # Braid SSE/events port
experiments_path = "/mnt/data/experiments/" # Where .braid folders are saved

[trigger_handler]
min_tracking_age = 0.1      # Min object age before triggering (s)
cooldown_period = 10.0    # Global cooldown between ZONE_ENTER (s)
z_min = 0.15                # Minimum z for trigger (m)
z_max = 0.25                # Maximum z for trigger (m)
heading_cone_deg = 45.0     # Heading tolerance from center-directed (degrees)
min_velocity = 0.01         # Min velocity threshold (m/s)
pre_zone_expansion = 0.01   # Extra metres added to each FOV edge for the pre-trigger zone

[camera]
active = true
resolution = [2112, 2112]
fps = 500

[opto_trigger]
active = true
port = "/dev/opto_trigger"
duration = [100, 200, 300]          # ms, randomly selected
intensity = [0, 51, 102, 153, 204, 255]
color = "red"

[liquid_lens]
# Activates automatically when camera is active
port = "/dev/ttyUSB1"
calibration_file = "calibrations/liquid_lens.csv"

[visual_stimuli]
active = true
config_file = "configs/visual_stimuli.toml"

[monitoring]
active = true
host = "0.0.0.0"
port = 5000
```

### configs/visual_stimuli.toml

Uses the Panda3D pipeline. Arena geometry goes in `[visual_stimuli.arena]`; each stimulus is a separate subsection.

```toml
[visual_stimuli]
active = true
log_file = "stim.csv"

[visual_stimuli.arena]
viewing_distance_cm = 25.0
window_x_offset = 3840          # X position of leftmost screen on desktop
# Physical screens left-to-right → compass direction
screen_mapping = ["South", "West", "North", "East"]
braid_heading_offset_rad = 0.0  # Braid value that maps to North screen
braid_heading_flip = false      # True if Braid heading runs opposite to arena

[visual_stimuli.background]
enabled = true
square_size_px = 40
density = 0.5
seed = 42

[visual_stimuli.looming]
enabled = true
initial_size_deg = 5.0
final_size_deg = 72.0
expansion_duration_ms = 300
hold_time_ms = 200
expansion_type = "exponential"  # "lv_ratio", "exponential", or "linear"
color = [0, 0, 0]
positions_deg = [-90, -45, 0, 45, 90]

[visual_stimuli.oscillating_square]
enabled = false
size_deg = 10.0
amplitude_deg = 30.0
frequency_hz = 2.0
duration_ms = 2000
positions_deg = [-45, 0, 45]
```

See `configs/visual_stimuli.example.toml` for all available options.

## Running an Experiment

1. Start Braid (tracking must be active)
2. Launch OptoFly:

```bash
uv run python main.py
```

The launcher checks for an active Braid recording folder (or starts one automatically), copies configuration files to the experiment folder, starts all enabled processes, and runs until Ctrl+C or the configured `experiment_duration` is reached.

## Calibration Tools

```bash
# Visual stimuli standalone test (Panda3D, no hardware)
python -m src.processes.visual --standalone

# BRAID-to-camera DLT calibration + multi-plane FOV (requires Braid + camera)
# Press 'f' to fit, 'p' to pin a FOV plane at the current Braid z, 's' to save.
# 1 plane -> flat [camera.FOV]; 2 planes -> [camera.FOV.near] + [camera.FOV.far]
uv run python -m src.tools.calibrate_braid_ximea --config configs/config.toml
```

The Panda3D pipeline uses `screen_mapping` in `[visual_stimuli.arena]` to assign compass directions to physical screens — no interactive calibration tool is required. Set `braid_heading_offset_rad` and `braid_heading_flip` to align Braid tracking coordinates with the arena.

See [docs/calibration.md](calibration.md) for full procedures.

## Testing

```bash
# Python unit tests
uv run pytest

# Camera integration test (requires hardware)
python tests/test_camera_integration.py

# Visual stimuli standalone (no hardware)
python -m src.processes.visual --standalone

# Simulate Braid tracking data (development)
python -m src.tools.braid_simulator
```
