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
max_velocity = 2.0          # Max velocity threshold, rejects tracking noise (m/s)
zone_timeout = 2.0          # Auto ZONE_EXIT if no updates for this long (s)

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

The launcher checks for an active Braid recording folder (or starts one automatically), then prompts interactively for experiment metadata: experimenter name, genetic cross, cross/F1/ATR dates, fly count, `experiment_duration` (hours, default 24), and free-text notes. It writes the answers to `experiment_data.toml` and an appended CSV row in the Braid folder, alongside a snapshot of `config.toml` and `visual_stimuli.toml`. Skip the prompt for quick tests:

```bash
uv run python main.py --skip-metadata
```

With `--skip-metadata`, `experiment_duration` defaults to 24 hours. The launcher starts all enabled processes and runs until Ctrl+C or `experiment_duration` elapses.

## Calibration Tools

```bash
# Visual stimuli standalone test (Panda3D, no hardware)
python -m src.processes.visual --standalone

# Camera FOV calibration (requires Braid + camera + liquid lens)
# Collect >= 4 edge points -> press 'n' to finalise plane (z auto-read from Braid).
# Press 's' to save flat [camera.FOV], or 'a' then repeat for frustum near/far.
uv run python -m src.tools.calibrate_braid_ximea --config configs/config.toml

# Braid heading -> arena screen heading (fits braid_heading_offset_rad / braid_heading_flip)
uv run python -m src.tools.calibrate_heading

# Liquid lens latency measurement (recommends system_latency for [liquid_lens.kalman])
uv run python -m src.tools.lens_latency_analyze /mnt/data/videos/<braid_dir>
```

The Panda3D pipeline assigns compass directions to physical screens via `screen_mapping` in `[visual_stimuli.arena]`; no interactive tool is needed for that part. `calibrate_heading.py` measures `braid_heading_offset_rad` and `braid_heading_flip` for you instead of setting them by hand: it shows a target dot on each screen in turn and fits the transform from where Braid sees a tracked object placed in front of each dot.

See [docs/calibration.md](calibration.md) for full procedures, including the liquid lens `(z, dpt)` calibration methods and frustum FOV calibration.

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
