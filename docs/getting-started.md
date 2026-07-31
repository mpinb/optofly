# Getting Started

This is the path from a fresh checkout to a running experiment. Each step is a short "do this" plus a link to the full reference doc for that step. Skim this page first to see the whole shape of the setup, then open the linked docs when you need the detail.

## Hardware you'll need

- Three or more **Basler cameras** for Braid's 3D tracking rig
- A **Ximea high-speed camera** for triggered video recording
- An **Optotune liquid lens** for dynamic autofocus
- An **Arduino** (Uno, Nano, or compatible) wired to a PicoBuck (or similar) constant-current LED driver, for optogenetic stimulation
- **Four displays** arranged around the arena for visual stimuli
- A laser pointer or bright LED (for camera FOV calibration)
- Optionally: an AprilTag target, for automated liquid lens calibration

## The pipeline, in order

1. Install OptoFly
2. Calibrate each tracking camera's intrinsics
3. Calibrate Braid's multi-camera 3D reconstruction
4. Confirm Braid is tracking
5. Configure OptoFly
6. Calibrate the liquid lens
7. Calibrate the camera trigger zone
8. Calibrate the arena heading
9. Bench-test the opto trigger
10. Run your first experiment

Steps 2-3 only need repeating when you physically move or add a tracking camera. Steps 6-8 only need repeating when you change the lens, the Ximea camera's position, or the arena's screen layout. Everything after that is just step 10, over and over.

## 1. Install OptoFly

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # skip if you already have uv
uv sync

sudo apt-get install -y build-essential libzmq3-dev ffmpeg
```

Install the XIMEA SDK separately; it's not a Python package:

```bash
sudo scripts/install_ximea_driver.sh
```

This downloads and installs the current XIMEA Linux PCIe driver, skipping the download if it already matches what's installed, and reminds you to reboot afterward: the PCIe driver won't take effect until you do. Full prerequisite list, including the conda/mamba alternative to `uv`: [setup reference](#installation-reference) below.

## 2. Calibrate each tracking camera's intrinsics

Every Basler camera in Braid's tracking rig needs its own intrinsic calibration (focal length, principal point, lens distortion) before Braid can triangulate anything. Use the separate [`basler-charuco-calibrator`](https://github.com/mpinb/basler-charuco-calibrator) repository:

```bash
git clone https://github.com/mpinb/basler-charuco-calibrator.git
cd basler-charuco-calibrator
uv sync
uv run python -m basler_charuco_calibrator
```

Move a ChArUco board around the frame until the coverage bars fill up, press `c` to calibrate. Repeat per camera. Full procedure: [calibration.md § Camera Intrinsic Calibration](calibration.md#camera-intrinsic-calibration).

## 3. Calibrate Braid's multi-camera 3D reconstruction

This combines the per-camera intrinsics from step 2 with the cameras' physical arrangement to produce the calibration Braid uses to triangulate 3D positions. It's part of Braid's own toolchain, so follow Braid's documentation for this step. Details on what this produces and where OptoFly needs it: [calibration.md § Braid Multi-Camera Calibration](calibration.md#braid-multi-camera-extrinsic-calibration).

## 4. Confirm Braid is tracking

Start Braid and check its web UI: flies (or a moved target) should show up as tracked 3D points. OptoFly's `BraidPublisher` connects to Braid's SSE endpoint at `http://<host>:8397/events`. Nothing downstream works until this is live.

## 5. Configure OptoFly

```bash
cp configs/config.example.toml configs/config.toml
cp configs/visual_stimuli.example.toml configs/visual_stimuli.toml
```

Both files are git-ignored, so local customization is safe. At minimum, set `[braid_publisher] host`/`events_port` to match your Braid server, and the serial `port` values under `[opto_trigger]` and `[liquid_lens]` to your actual devices. Full config reference: [below](#configuration-reference).

## 6. Calibrate the liquid lens

Maps fly z-position to lens diopter, manually or with an automated AprilTag-triangulation tool. Full procedure: [calibration.md § Liquid Lens Calibration](calibration.md#liquid-lens-calibration).

## 7. Calibrate the camera trigger zone

Maps Braid's x/y/z tracking volume to the Ximea camera's field of view, so `TriggerHandler` knows when a fly is actually in frame. Full procedure: [calibration.md § Camera FOV Calibration](calibration.md#camera-fov-calibration) (and [Frustum FOV Calibration](calibration.md#frustum-fov-calibration) if a flat FOV isn't accurate enough across your z-range).

## 8. Calibrate the arena heading

Aligns Braid's heading coordinate frame with the four-screen Panda3D arena, so visual stimuli appear in the right place relative to the fly. Full procedure: [calibration.md § Panda3D Heading Calibration](calibration.md#panda3d-heading-calibration).

## 9. Bench-test the opto trigger

Before connecting to live flies, verify each LED channel fires correctly over serial. Full procedure: [opto-trigger.md § Bench Testing](opto-trigger.md#bench-testing).

## 10. Run your first experiment

With Braid running and tracking:

```bash
uv run python main.py
```

The launcher starts a fresh Braid recording via Braid's callback API, then prompts for experiment metadata (experimenter, cross, dates, fly count, duration, notes) before starting all enabled processes. Skip the prompt for a quick test run:

```bash
uv run python main.py --skip-metadata
```

It runs until Ctrl+C or the configured `experiment_duration` elapses. Full details: [below](#running-an-experiment).

---

## Installation Reference

**Python environment (choose one):**

Option 1: uv (recommended)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

Option 2: conda/mamba
```bash
mamba env create -f environment.yml
conda activate optofly_env
```

**System dependencies** (Ubuntu/Debian)
```bash
sudo apt-get install -y \
    build-essential \
    libzmq3-dev \
    ffmpeg
```

**XIMEA SDK** (for camera support)

```bash
sudo scripts/install_ximea_driver.sh
```

Downloads and installs the current [XIMEA Linux PCIe driver](https://www.ximea.com/support/wiki/apis/XIMEA_Linux_Software_Package). Safe to re-run: it skips the download if the installed version already matches. Reboot afterward for the PCIe driver to take effect.

## Configuration Reference

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
cooldown_period = 10.0      # Global cooldown between ZONE_ENTER (s)
z_min = 0.10                # Minimum z for trigger (m)
z_max = 0.25                # Maximum z for trigger (m)
heading_cone_deg = 30.0     # Heading tolerance from center-directed (degrees)
min_velocity = 0.01         # Min velocity threshold (m/s)
max_velocity = 1.0          # Max velocity threshold, rejects tracking noise (m/s)
zone_timeout = 3.0          # Auto ZONE_EXIT if no updates for this long (s)
opto_zone_scale = 0.5       # Opto fires only once the fly reaches this fraction of the FOV, centered (0-1]
visual_zone_scale = 1.0     # Visual fires at this fraction of the FOV, centered (0-1]; 1.0 = same as camera FOV

[camera]
active = true
resolution = [2112, 2112]
fps = 500

[opto_trigger]
active = false                     # set true once the Arduino is wired up
port = "/dev/opto_trigger"         # required even when active = false (AppConfig validates all sections)
duration = [100, 200, 300]         # ms — balanced randomization per trigger
intensity = [0, 51, 102, 153, 204, 255]
color = "red"

[liquid_lens]
# Activates automatically when camera is active — no active flag of its own
port = "/dev/optotune_icc1c"  # udev symlink; see configs/config.example.toml
calibration_file = "calibrations/liquid_lens.csv"

[visual_stimuli]
active = true
config_file = "configs/visual_stimuli.toml"
```

**Operational notes:**
- An existing rig `configs/config.toml` (git-ignored, not touched by upgrades) that predates the opto/visual zone split has none of `opto_zone_scale`, `visual_zone_scale`, `opto_enter_topic`, or `visual_enter_topic`. On the next run, opto will silently start firing at 50% of the FOV instead of the full FOV (the `opto_zone_scale` default). This is not a bug — just worth knowing before your first run after upgrading, since the recorded video will look identical but the LED will fire later than it used to.
- It's now possible for a trial to have a recording plus lens/visual latency rows but *no* opto latency row at all, if the fly left the outer zone before ever reaching the smaller opto zone. This is expected, not a dropped message — check `opto_zone_scale` if you expect opto to fire on every trial.

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

The launcher starts a fresh Braid recording via the callback API (it never reuses an existing same-day folder), then prompts interactively for experiment metadata: experimenter name, genetic cross, cross/F1/ATR dates, fly count, `experiment_duration` (hours, default 24), and free-text notes. It writes the answers to `experiment_data.toml` in the Braid folder (alongside a snapshot of `config.toml` and `visual_stimuli.toml`) and appends a row to the central `~/optofly_experiments.csv` log in your home directory. Skip the prompt for quick tests:

```bash
uv run python main.py --skip-metadata
```

With `--skip-metadata`, `experiment_duration` defaults to 24 hours. The launcher starts all enabled processes and runs until Ctrl+C or `experiment_duration` elapses. Use `--config <path>` to run against a different TOML file than `configs/config.toml`.

Note that `OptoTriggerWorker` starts even when `[opto_trigger] active = false` (it drives the arena backlight). A missing Arduino is then survivable — the run continues without the backlight; with `active = true` it aborts startup instead.

## Development

```bash
# Python unit tests
uv run pytest

# Camera preflight (no hardware needed — reports what's missing)
uv run python -c "from src.processes.camera import check_camera_prerequisites as c; \
[print(k, v) for k, v in c('configs/config.toml').items()]"

# Visual stimuli standalone (no hardware)
uv run python -m src.visual --standalone

# Simulate Braid tracking data (development)
uv run python -m src.tools.braid_simulator
```

## Where to Go Deeper

| Topic | Doc |
|---|---|
| Every calibration procedure in detail | [calibration.md](calibration.md) |
| System architecture, data flow, ZMQ topology | [architecture.md](architecture.md) |
| Ximea high-speed camera system | [camera.md](camera.md) |
| Arduino LED firmware and serial protocol | [opto-trigger.md](opto-trigger.md) |
| Panda3D visual stimuli, writing new stimuli | [visual-stimuli-panda3d.md](visual-stimuli-panda3d.md) |
| Common issues and fixes | [troubleshooting.md](troubleshooting.md) |
