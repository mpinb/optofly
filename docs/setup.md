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

## Build Camera Binary

```bash
cd rust/ximea_camera
cargo build --release
cd ../..
```

Binary will be at `rust/ximea_camera/target/release/ximea_camera`.

## Configuration

Copy the example configs and customize them:

```bash
cp config.example.toml config.toml
cp visual_stimuli.example.toml visual_stimuli.toml
```

Both files are git-ignored so local customizations are safe.

### config.toml

Key sections:

```toml
[braid_publisher]
url = "http://10.40.80.6:8397"              # Braid server address
experiments_path = "/mnt/data/experiments/" # Where .braid folders are saved

[trigger_handler]
min_trajectory_time = 1.0   # Min tracking duration before triggering (s)
min_trigger_interval = 10.0 # Cooldown between triggers (s)
radius = 0.05               # Trigger zone radius (m)
z_lim = [0.15, 0.25]        # Vertical trigger zone limits (m)
heading_cone_deg = 30.0     # Heading threshold (degrees from center-directed)
min_velocity = 0.01         # Min velocity threshold (m/s)

[camera]
active = true
resolution = [2016, 2016]
fps = 500
pre_trigger_time = 0.5
post_trigger_time = 1.5

[opto_trigger]
active = true
port = "/dev/opto_trigger"
duration = [100, 200, 300]          # ms, randomly selected
intensity = [0, 51, 102, 153, 204, 255]
color = "red"

[liquid_lens]
active = false
port = "/dev/ttyUSB1"
calibration_file = "calibrations/liquid_lens.csv"

[visual_stimuli]
active = true
config_file = "visual_stimuli.toml"
```

### visual_stimuli.toml

```toml
[display]
window_width = 7680
window_height = 1080
arena_center_to_screen_cm = 25.0

[visual_stimuli.static]
enabled = true
pattern_density = 0.3
random_seed = 42

[visual_stimuli.looming]
enabled = true
initial_size_deg = [5.0, 10.0, 15.0]
final_size_deg = 72.0
expansion_duration_ms = [300, 500, 700]
positions_deg = [-90, 0, 90]
```

See the example files for all available options.

## Running an Experiment

1. Start Braid recording (creates the `.braid` folder)
2. Launch OptoFly:

```bash
uv run python main.py
# or
python main.py
```

The launcher checks for a Braid recording folder, loads configuration, starts all enabled processes, and runs until Ctrl+C.

## Testing

```bash
# Python unit tests
uv run pytest

# Specific test file
uv run pytest tests/test_config.py -v

# Camera integration test
python tests/test_camera_integration.py

# Visual stimuli standalone (no hardware)
python -m src.processes.visual --standalone

# Rust tests
cd rust/ximea_camera && cargo test
```
