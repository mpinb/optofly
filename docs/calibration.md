# Calibration

## Visual Stimuli Calibration

The visual stimuli system needs to know how fly heading (from Braid tracking) maps to pixel positions on the display. Two steps are required.

### Step 1: Screen Identification

Identifies which physical screen corresponds to which display port.

```bash
python -m src.processes.visual --calibrate
```

Labels appear on each screen showing "Screen 1", "Screen 2", etc. with display port names. Identify which screen is North/East/South/West in your arena, then press ESC. Update `screen_mapping` in `configs/visual_stimuli.toml` if needed.

### Step 2: Heading-to-Pixel Mapping

Creates a mapping from Braid tracking coordinates to screen pixel positions.

```bash
python -m src.processes.visual --calibrate-mapping
```

For each of the 4 calibration positions (one per screen):
1. A red circle appears at the calibration position
2. Place a tracked object in the arena so it faces directly toward the circle
3. Note the object's x, y coordinates from the Braid browser interface
4. Enter those values when prompted in the terminal
5. Press Enter to advance to the next position

**Output files:**

| File | Description |
|------|-------------|
| `calibrations/heading_mapping_data.csv` | Raw calibration data (pixel_x, braid_x, braid_y) |
| `calibrations/heading_mapping_model.npz` | Interpolation model used at runtime |

**Apply calibration in `configs/visual_stimuli.toml`:**
```toml
[visual_stimuli]
calibration_mapping_file = "calibrations/heading_mapping_model.npz"
use_empirical_calibration = true
```

### Step 3: Verify Calibration

```bash
python -m src.processes.visual --test-calibration
```

A red circle sweeps clockwise around all screens. Verify:
- The circle moves smoothly from screen to screen
- Direction labels match your physical arrangement
- The circle wraps at the edges (South edge connects to West edge)

Controls: SPACE (pause/resume), LEFT/RIGHT (manual adjust when paused), R (reset to 0), ESC (exit).

### Calibration Theory

**Coordinate systems:**

- Braid space: (x, y, z) in meters, heading = `arctan2(yvel, xvel)`, origin at arena center
- Display space: (0, 0) at bottom-left, (7680, 1080) at top-right, four 1920x1080 screens, cylindrical wrap

The calibration builds a lookup table: `Braid heading (radians) → Screen pixel X`. At runtime, `GeometryUtils.heading_to_pixel_x()` linearly interpolates between calibration points.

### Manual Calibration File

If you need to create a calibration file manually:

```csv
pixel_x,braid_x,braid_y
0,-0.05,0.02
640,-0.04,0.03
```

The `.npz` model contains two arrays:
- `headings`: sorted heading values in radians
- `pixels`: corresponding pixel X positions

---

## Liquid Lens Calibration

Maps fly z-position (meters) to lens focal power (diopters) so the lens tracks focus as the fly moves vertically.

### Prerequisites

- Lens connected and serial port confirmed (default: `/dev/ttyUSB1`)
- Braid running with a tracked object visible
- OptoFly environment activated

### Procedure

1. Place a target at a known height in the arena and note its z-coordinate from Braid.

2. Connect to the lens and switch to focal power mode:

   ```python
   from src.hardware.lens import LensDriver
   lens = LensDriver(port="/dev/ttyUSB1")
   lens.to_focal_power_mode()
   ```

3. Adjust diopters until the target is in focus:

   ```python
   lens.set_diopter(2.0)  # adjust as needed
   ```

4. Record the `(z, dpt)` pair.

5. Repeat across the full z-range of your arena (at least 5-10 points).

6. Write the collected data to `calibrations/liquid_lens.csv`:

   ```csv
   z,dpt
   0.05,2.3
   0.10,1.9
   0.15,1.5
   ```

### Configuration

Point to the calibration file and choose a model in `configs/config.toml`:

```toml
[liquid_lens]
port = "/dev/optotune_ld"
calibration_file = "calibrations/liquid_lens.csv"
calibration_model = "quadratic"   # linear | quadratic | power | inverse
```

### How It Works

At startup, `LensCalibration` reads the CSV and fits the selected model to the `(z, dpt)` pairs:

| Model | Formula | Notes |
|-------|---------|-------|
| `linear` | `dpt = a·z + b` | Fast, R²≈0.94 |
| `quadratic` | `dpt = a·z² + b·z + c` | Recommended, R²≈0.995 |
| `power` | `dpt = a·z^b + c` | Physically motivated |
| `inverse` | `dpt = a/(z − b) + c` | Thin-lens theory |

`linear` and `quadratic` use `numpy.polyfit`; `power` and `inverse` use `scipy.optimize.curve_fit`. The fitted coefficients are captured in a lambda so `get_dpt(z)` is pure floating-point arithmetic — no lookup table, no numpy on the hot path. `z` is clamped to the calibration range to prevent extrapolation.

### Troubleshooting

- **Serial port not found**: run `ls -l /dev/ttyUSB*` and check permissions with `sudo chmod 666 /dev/ttyUSB1`
- **Lens not responding**: verify the handshake by running `LensDriver` interactively with `debug=True`
- **Poor focus across z-range**: add more calibration points, especially at the extremes

---

## BRAID-to-Ximea Calibration

Maps BRAID 3D world coordinates (x, y, z in metres) to Ximea camera pixel positions (u, v). Used at runtime to centre the depth-from-focus ROI on the fly. Also computes the camera field of view (FOV) automatically.

**Why a laser pointer?** BRAID tracks simple round blobs (dark-on-light or light-on-dark). Complex textured targets (checkerboards, etc.) are not detectable. A laser dot projected into the arena is visible to both BRAID (as a bright blob) and the Ximea camera simultaneously, making it the reliable ground-truth target for collecting correspondences.

### Prerequisites

- Ximea camera connected and live
- BRAID running and tracking
- OptoFly environment activated (`uv sync`)
- A laser pointer you can aim at multiple positions in the arena volume

### Point Layout

The DLT projection matrix has 11 degrees of freedom and requires at least 6 point correspondences. For best accuracy and FOV coverage:

- **You must click all 4 frame corners** (top-left, top-right, bottom-right, bottom-left). Corner points span the full sensor and are essential for an accurate FOV computation.
- **You must add at least 2 interior points** anywhere else in the frame to meet the 6-point minimum.

Points spread across the full frame and across different z heights improve conditioning. More points = better accuracy.

### Procedure

1. Ensure BRAID is running and tracking.

2. Launch the calibration tool:

   ```bash
   python -m src.tools.calibrate_braid_ximea --config configs/config.toml
   ```

3. The tool opens an OpenCV window with the live Ximea feed. The current BRAID position is shown as an overlay — no separate tracking window needed.

4. For each calibration point:
   - Aim the laser pointer at a position in the arena and hold it steady.
   - Wait for BRAID to lock onto the laser dot — the tool streams BRAID updates live and shows the current tracked position in the terminal.
   - **Left-click** the laser dot's pixel in the camera image.
   - Enter the BRAID (x, y, z) coordinates when prompted in the terminal.
   - The point is drawn on the frame (red = corner, blue = interior).

5. Collect all 4 corners first (aim the laser near each corner of the camera FOV), then at least 2 more interior points at different heights. The status line updates:
   - `"3 corner point(s) still needed"`
   - `"All 4 corners collected — add ≥2 more interior points"`
   - `"≥6 points collected — press 'f' to fit"`

6. Press **`f`** to fit the DLT matrix. Reprojection errors are printed; < 5 px is good.

### Key Bindings

| Key | Action |
|-----|--------|
| Left-click | Add calibration point (prompts for BRAID coordinates) |
| `f` | Fit the DLT matrix (requires ≥ 6 points) |
| `v` | Visualise the FOV: back-projects frame corners and draws an orange rectangle |
| `s` | Save the calibration; offers to update `[camera.FOV]` in `config.toml` |
| `d` | Delete the last point |
| `r` | Reset all points |
| `q` / ESC | Quit |

### Computing the Camera FOV

After fitting (`f`), press **`v`** to visualise the field of view. The tool:

1. Back-projects each of the 4 frame corners through the DLT matrix at a reference z height (you are prompted to confirm or change this).
2. Draws an orange rectangle on the frame showing the sensor boundary in world space.
3. Prints the derived `x_min`, `x_max`, `y_min`, `y_max` values.

When you save with **`s`**, the tool asks:

```
Update [camera.FOV] in configs/config.toml? [y/N]
```

Answer `y` to write the values directly into the config file. No manual editing required.

### Enabling the Calibration

Add the calibration file path to `configs/config.toml`:

```toml
[camera]
braid_ximea_calibration_file = "calibrations/braid_to_ximea.npz"
```

The tool offers to write this automatically when you press `s`.

### Theory

`BraidToXimeaCalibration` (`src/utils/calibration.py`) fits a 3×4 projection matrix **P** (11 DOF) via SVD such that:

```
[u, v, 1]ᵀ ∝ P @ [x, y, z, 1]ᵀ
```

from N ≥ 6 point correspondences. The system is solved as a 2N×12 linear system (each point contributes 2 equations) and the solution is the right singular vector corresponding to the smallest singular value.

**Backprojection at known z**: given pixel (u, v) and z, `backproject()` solves a 3×3 linear system to recover (x, y) in world space. This is used both for FOV computation and for placing the DFF ROI at the fly's projected pixel position during recording.

### Output File

`calibrations/braid_to_ximea.npz` contains:

| Key | Description |
|-----|-------------|
| `P` | 3×4 DLT projection matrix |
| `points_3d` | Collected world points (N×3) |
| `points_2d` | Collected pixel points (N×2) |
| `reprojection_errors` | Per-point pixel error |
| `mean_reprojection_error` | Mean pixel error |

### Troubleshooting

- **"Need at least 6 points"**: you must collect all 4 corners plus at least 2 interior points before fitting.
- **High reprojection error (> 10 px)**: delete outlier points with `d`, re-collect them, and refit. Ensure the BRAID coordinates were read while the laser dot was stationary and BRAID had a stable lock on it.
- **FOV looks wrong**: verify `z_ref` matches the typical flight height (not the floor or ceiling). Re-press `v` with a different z if needed.
- **"Singular matrix" error during backprojection**: the DLT matrix is degenerate — usually caused by all points being nearly coplanar in 3D. Add points at different z heights.
