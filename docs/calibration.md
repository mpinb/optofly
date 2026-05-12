# Calibration

## Visual Stimuli Calibration (Legacy Pyglet Pipeline)

> The Panda3D pipeline (`src/visual/`) does not use this interactive calibration.
> Screen assignment is configured via `screen_mapping` in `[visual_stimuli.arena]` of
> `configs/visual_stimuli.toml`. Set `braid_heading_offset_rad` and `braid_heading_flip`
> to align Braid heading with the arena compass.
>
> The steps below apply only to the legacy pyglet pipeline (`src/stimuli/`), invoked via
> `python -m src.processes.visual --calibrate*`.

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

---

## Frustum FOV Calibration

### When to Use This

The flat `[camera.FOV]` calibration (above) uses a single set of x/y bounds applied at all z heights. This is correct for a telecentric lens, but a standard (non-telecentric) liquid lens produces a field of view that grows with distance — the further the fly is from the camera, the wider the visible area. A flat FOV either misses flies at the far edge of the volume or triggers spuriously on flies outside the actual frame.

The frustum calibration measures the FOV at two z reference planes (near and far) and stores both. At runtime, `TriggerHandler` linearly interpolates the bounds at the fly's actual z, giving a perspective-correct trigger zone.

**Use this calibration if:**
- You see flies triggering outside the visible frame (FOV too wide)
- You see flies in-frame that don't trigger (FOV too narrow)
- The discrepancy worsens at higher or lower z positions

### Prerequisites

- Ximea camera connected and live
- Braid running with the full ZMQ stack (`uv run python main.py` or at minimum `BraidPublisher`)
- Liquid lens connected with `calibrations/liquid_lens.csv` already built (see Liquid Lens Calibration above)
- A laser pointer you can hold at known heights in the arena
- OptoFly environment activated (`uv sync`)

### Choosing z-planes

Pick two z heights that bracket the fly's typical flight range:

- **Near plane** (`--near-z`): the lowest z flies are expected to trigger at. A reasonable starting point is the bottom of your trigger zone (`z_min` in `config.toml`).
- **Far plane** (`--far-z`): the highest z flies are expected to trigger at — your `z_max` value, or the top of the arena.

The wider the spread between the two planes, the more accurate the interpolation will be across the full volume. A 10–15 cm separation is usually sufficient. The far-plane bounds will always be larger than the near-plane bounds; if they are not, the lens is not a normal (converging) imaging setup.

### Procedure

1. Ensure Braid is running and the ZMQ publisher is live:

   ```bash
   uv run python main.py   # or just BraidPublisher standalone
   ```

2. Launch the calibration tool, passing your two z reference heights:

   ```bash
   uv run python -m src.tools.calibrate_frustum_fov --near-z 0.10 --far-z 0.25
   ```

   The tool will print the diopter values it will command for each plane and confirm the lens and camera opened successfully.

3. **Phase 1 — Near plane.** The lens focuses to `z_near`. Hold the laser pointer at approximately that height in the arena and wait for Braid to acquire it (the BRAID position is shown live in the overlay).

   Sweep the laser to each of the four extremes of the camera frame and press **SPACE** at each position:

   - Leftmost point still visible in the frame
   - Rightmost point still visible in the frame
   - Top-most point still visible in the frame
   - Bottom-most point still visible in the frame

   The overlay shows a live bounding-box estimate (in metres) that updates with each new point. Add more than 4 points if the live estimate seems noisy — the tool takes the min/max of all recorded x,y values, so more points only help.

   Press **`n`** when satisfied (requires ≥ 4 points).

4. **Phase 2 — Far plane.** The lens refocuses to `z_far`. Raise the laser pointer to approximately that height and repeat the same four-edge measurements. Press **`n`** again when done.

5. Press **`s`** to review the computed bounds and write them to `config.toml`:

   ```
   Write [camera.FOV.near] and [camera.FOV.far] to configs/config.toml? [y/N]
   ```

   Answer `y`. The tool replaces the old flat `[camera.FOV]` keys with the two sub-tables in-place, preserving all other sections and comments.

6. Verify the written values:

   ```bash
   grep -A 15 "\[camera\.FOV\]" configs/config.toml
   ```

   The far-plane bounds should be visibly wider than the near-plane bounds. If they are identical or reversed, re-run the calibration.

7. Restart the main stack and confirm `TriggerHandler` logs frustum mode at startup:

   ```
   TriggerHandler: frustum FOV mode  near z=0.1000 m  far z=0.2500 m
   ```

### Key Bindings

| Key | Action |
|-----|--------|
| `SPACE` | Auto-detect bright spot and record the current Braid x,y |
| Left-click | Manual fallback — record Braid x,y at the clicked pixel position |
| `u` | Undo the last recorded point in the current phase |
| `n` | Advance to the next phase (requires ≥ 4 points in the current phase) |
| `s` | Save both planes to config.toml (requires both phases complete) |
| `q` | Quit without saving |

### Troubleshooting

- **Bright spot not detected / SPACE has no effect**: the laser may be too dim or too bright relative to the threshold. Try a lower value for a dim laser or a higher value to suppress background reflections:

  ```bash
  uv run python -m src.tools.calibrate_frustum_fov --near-z 0.10 --far-z 0.25 --threshold 150
  ```

  The cyan circle in the overlay shows the detected spot in real time — use it to confirm detection before pressing SPACE.

- **"No BRAID fix"**: Braid is not tracking the laser. Make sure the laser dot is inside the tracking volume and Braid has acquired it before recording. The BRAID position readout in the overlay turns white when a fix is active.

- **Lens does not move between phases**: check that the serial port in `[liquid_lens] port` in `config.toml` is correct (`ls -l /dev/ttyUSB*`). The tool will print an error at startup if it cannot open the port.

- **Far-plane bounds narrower than near-plane bounds**: the laser was probably held at the wrong height during one of the phases, or the Braid z coordinate was far from the commanded z. Re-run the calibration and confirm the laser height visually.

- **Trigger zone still looks wrong after calibration**: the frustum interpolates linearly between the two planes. If the optical distortion is non-linear across the z range, add a finer trigger zone (narrow `z_min`/`z_max`) so the fly spends less time in the interpolated region.

### Output

The tool writes directly to `configs/config.toml`, replacing the `[camera.FOV]` section:

```toml
[camera.FOV]
# Frustum mode — generated by calibrate_frustum_fov.py

[camera.FOV.near]
z     = 0.1000      # z where these bounds were measured
x_min = -0.01500
x_max = 0.02500
y_min = -0.01800
y_max = 0.03000

[camera.FOV.far]
z     = 0.2500      # z where these bounds were measured
x_min = -0.02180
x_max = 0.03900
y_min = -0.02500
y_max = 0.04100
```

To revert to flat mode, replace both sub-tables with flat keys:

```toml
[camera.FOV]
x_min = -0.0218
x_max = 0.039
y_min = -0.025
y_max = 0.041
```
