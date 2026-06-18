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

## Camera FOV Calibration

Measures the camera field-of-view at one or two z-planes so that `[camera.FOV]` (flat) or `[camera.FOV.near]` / `[camera.FOV.far]` (frustum) can be written to `config.toml`.

The z for each plane is read automatically from Braid — the tool computes the median of the Braid z values recorded during point collection, so you never have to type a height manually.

### Prerequisites

- Ximea camera connected and live
- Braid running and tracking
- Liquid lens connected with `calibrations/liquid_lens.csv` built (see Liquid Lens Calibration above)
- A laser pointer or bright LED you can hold at the frame edges
- OptoFly environment activated (`uv sync`)

### Procedure

1. Launch the calibration tool:

   ```bash
   uv run python -m src.tools.calibrate_braid_ximea --config configs/config.toml
   ```

2. Hold the laser at your desired height. Sweep it to the four edges of the camera frame — left edge, right edge, top edge, bottom edge. Press **SPACE** at each position (or left-click as a fallback). The tool records the Braid (x, y, z) at each press.

3. Collect at least 4 boundary points. The live overlay shows the estimated x/y span and updates with each new point.

4. Press **`n`** to finalise the plane. The tool:
   - Computes the plane z as the **median** of all recorded Braid z values.
   - Refocuses the liquid lens to that z.
   - Prints the derived `x_min`, `x_max`, `y_min`, `y_max` bounds.

5. Choose next action:
   - Press **`s`** → saves a flat `[camera.FOV]` and quits.
   - Press **`a`** → adds a second plane. Move the laser to the other height and repeat steps 2–4. Then press **`s`** to save as `[camera.FOV.near]` + `[camera.FOV.far]`.

### Key Bindings

| Key | Action |
|-----|--------|
| `SPACE` | Auto-detect bright spot and record Braid x, y, z |
| Left-click | Manual fallback — records Braid x, y, z at clicked pixel |
| `u` | Undo the last recorded point |
| `n` | Finalise the current plane (requires ≥ 4 points) |
| `a` | Add a second plane (available after plane 1 is finalised) |
| `s` | Save FOV to `config.toml` and quit |
| `q` | Quit without saving |

### Troubleshooting

- **Bright spot not detected**: adjust `--threshold` (default 200). The cyan circle in the overlay shows the detected spot — confirm it sits on the laser dot before pressing SPACE.
- **"No Braid fix"**: Braid is not tracking the laser. Ensure the dot is inside the tracking volume before recording.
- **FOV looks wrong after saving**: re-run and ensure the laser is at the edges of the frame (not the centre). Add more than 4 points if the live bounds estimate is noisy.
- **Lens does not refocus**: check the serial port in `[liquid_lens] port` in `config.toml`.

---

## Frustum FOV Calibration

### When to Use This

The flat `[camera.FOV]` calibration uses a single set of x/y bounds applied at all z heights. A standard liquid lens produces a field of view that grows with distance — the further the fly is from the camera, the wider the visible area. A flat FOV either misses flies at the far edge of the volume or triggers spuriously on flies outside the actual frame.

The frustum calibration captures FOV bounds at two z heights and stores both. At runtime, `TriggerHandler` linearly interpolates the bounds at the fly's actual z, giving a perspective-correct trigger zone.

**Use this calibration if:**
- You see flies triggering outside the visible frame (FOV too wide)
- You see flies in-frame that don't trigger (FOV too narrow)
- The discrepancy worsens at higher or lower z positions

### Prerequisites

Same as Camera FOV Calibration above.

### Procedure

Frustum calibration is an extension of the standard FOV calibration. After completing plane 1, press **`a`** instead of **`s`**:

1. Run `calibrate_braid_ximea` and collect ≥ 4 edge points at the **near height** (lowest typical flight z). Press **`n`** to finalise.

2. Press **`a`** to add a second plane.

3. Move the laser to the **far height** (highest typical flight z). Collect ≥ 4 edge points and press **`n`**.

4. Press **`s`** to save:

   ```
   Write [camera.FOV.near] (z=0.1000 m) and [camera.FOV.far] (z=0.2500 m) to configs/config.toml? [y/N]
   ```

   Answer `y`. The lower-z plane is automatically assigned as near, higher-z as far.

5. Verify and restart:

   ```bash
   grep -A 15 "\[camera\.FOV\]" configs/config.toml
   ```

   The far-plane bounds should be visibly wider than the near-plane bounds. Restart the main stack and confirm:

   ```
   TriggerHandler: frustum FOV mode  near z=0.1000 m  far z=0.2500 m
   ```

### Troubleshooting

- **Far-plane bounds narrower than near-plane bounds**: the laser was at the wrong height when you collected points for one of the planes. Rerun the tool.

- **Trigger zone still looks wrong**: the frustum interpolates linearly. If distortion is non-linear, narrow `z_min`/`z_max` so the fly spends less time in the interpolated region.

### Output

`calibrate_braid_ximea` writes directly to `configs/config.toml`, replacing the `[camera.FOV]` section:

```toml
[camera.FOV]
# Frustum mode — generated by calibrate_braid_ximea.py

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
