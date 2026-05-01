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
refractory_period = 10.0    # Global cooldown between ZONE_ENTER (s)
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

```toml
[visual_stimuli]
active = true
window_x_offset = 3840
window_width = 7680
window_height = 1080
target_fps = 60
arena_center_to_screen_cm = 25.0

[visual_stimuli.static]
enabled = true
pattern_density = 0.5
random_seed = 42

[visual_stimuli.looming]
enabled = true
initial_size_deg = 5.0
final_size_deg = 72.0
expansion_duration_ms = 500
positions_deg = [-90, 0, 90]

[visual_stimuli.vertical_bar]
enabled = false
bar_width_deg = 20.0
positions_deg = [90, 0, 180]
```

See the example files for all available options.

## Running an Experiment

1. Start Braid (tracking must be active)
2. Launch OptoFly:

```bash
uv run python main.py
```

The launcher checks for an active Braid recording folder (or starts one automatically), copies configuration files to the experiment folder, starts all enabled processes, and runs until Ctrl+C or the configured `experiment_duration` is reached.

## Calibration Tools

```bash
# Visual stimuli heading → pixel mapping (interactive)
python -m src.processes.visual --calibrate
python -m src.processes.visual --calibrate-mapping
python -m src.processes.visual --test-calibration

# BRAID-to-camera DLT calibration (interactive, requires Braid + camera)
python -m src.tools.calibrate_braid_ximea --config configs/config.toml

# Offline mode (static image, no hardware)
python -m src.tools.calibrate_braid_ximea --image /path/to/frame.png --config configs/config.toml
```

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
