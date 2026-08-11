# Calibration

The sections below are ordered the way you'll actually do them: each one depends on the one before it. If you're setting up a new rig, work through them top to bottom. If you're fixing one part of an already-working rig, jump straight to that section.

| # | Calibration | Depends on | Tool |
|---|---|---|---|
| 1 | [Camera Intrinsic Calibration](#camera-intrinsic-calibration) | A Basler tracking camera | [`basler-charuco-calibrator`](https://github.com/mpinb/basler-charuco-calibrator) (separate repo) |
| 2 | [Braid Multi-Camera Calibration](#braid-multi-camera-extrinsic-calibration) | Step 1, done for every tracking camera | Braid's own tooling |
| 3 | [Liquid Lens Calibration](#liquid-lens-calibration) | Braid tracking live | `optotune_lens`, or [`liquid-lens-calibration`](https://github.com/mpinb/liquid-lens-calibration) (separate repo) |
| 4 | FOV Calibration — [frustum](#frustum-fov-calibration) (recommended) or [flat](#camera-fov-calibration) (quick/throwaway rigs only) | Step 3 | `src.tools.calibrate_braid_ximea` |
| 5 | [Panda3D Heading Calibration](#panda3d-heading-calibration) | Braid tracking live | `src.tools.calibrate_heading` |

## Camera Intrinsic Calibration

Before Braid can triangulate 3D fly positions from multiple 2D camera views, every tracking camera needs its own intrinsic calibration: focal length, principal point, and lens distortion. This is the first thing to do on a new rig. It's also the only step that touches each camera in isolation; everything after this involves the whole multi-camera rig working together.

OptoFly doesn't include a tool for this. Use the separate [`basler-charuco-calibrator`](https://github.com/mpinb/basler-charuco-calibrator) repository, which drives a Basler camera via `pypylon`, shows a live detection overlay of a ChArUco board, and auto-captures frames as you move the board to cover the frame:

```bash
git clone https://github.com/mpinb/basler-charuco-calibrator.git
cd basler-charuco-calibrator
uv sync
uv run python -m basler_charuco_calibrator
```

Move the board around in front of the camera until all four coverage bars (horizontal position, vertical position, size, skew) in the overlay reach 70%, then press `c` to calibrate and `q` to quit. Repeat once per tracking camera. Each run writes a ROS-format YAML (`Basler-<serial>.yaml`) with the camera matrix and distortion coefficients. See that repo's README for the full key-binding and config reference.

Do this for every Basler camera in the tracking rig before moving to step 2.

## Braid Multi-Camera (Extrinsic) Calibration

The per-camera intrinsics from step 1 feed into Braid's own multi-camera extrinsic calibration, which works out where each camera sits and points relative to the others and produces the `multi_camera_reconstructor` XML that Braid uses to triangulate 3D positions from synchronized 2D detections. This step is part of the Braid/strand-braid toolchain, not OptoFly. Follow Braid's own calibration documentation for the procedure.

The output XML matters to OptoFly in one place: the external [`liquid-lens-calibration`](https://github.com/mpinb/liquid-lens-calibration) tool (see [Liquid Lens Calibration](#liquid-lens-calibration) below) takes this file as its `--calibration` argument so it can triangulate a target's true `z` using the same camera geometry Braid uses for tracking.

Once this step is done, Braid should be tracking flies in 3D and publishing over its SSE endpoint (`http://<host>:8397/events`). Confirm that in Braid's own web UI before continuing. Every calibration step below assumes Braid is already running and tracking.

## Liquid Lens Calibration

Maps fly z-position (meters) to lens focal power (diopters) so the lens tracks focus as the fly moves vertically.

### Prerequisites

- Lens connected and serial port confirmed (required `port` key in `[liquid_lens]`; the example config uses the udev symlink `/dev/optotune_icc1c` — see the udev comments in `configs/config.example.toml`)
- Braid running with a tracked object visible
- OptoFly environment activated

### Manual Procedure

1. Place a target at a known height in the arena and note its z-coordinate from Braid.

2. Connect to the controller and switch to focal power mode:

   ```python
   from optotune_lens import ICC1C
   lens = ICC1C(port="/dev/optotune_icc1c")
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
port = "/dev/optotune_icc1c"
calibration_file = "calibrations/liquid_lens.csv"
calibration_model = "quadratic"   # linear | quadratic | power | inverse
```

### How It Works

At startup, `LensCalibration` reads the CSV and fits the selected model to the `(z, dpt)` pairs:

| Model | Formula | Notes |
|-------|---------|-------|
| `linear` | `dpt = a·z + b` | Fast, R²≈0.94 |
| `quadratic` | `dpt = a·z² + b·z + c` | Recommended, R²≈0.995 |
| `power` | `dpt = a·z^b + c` | |
| `inverse` | `dpt = a/(z − b) + c` | Physically motivated |

`linear` and `quadratic` use `numpy.polyfit`; `power` and `inverse` use `scipy.optimize.curve_fit`. The fitted coefficients are captured in a lambda so `get_dpt(z)` is pure floating-point arithmetic — no lookup table, no numpy on the hot path. `z` is clamped to the calibration range to prevent extrapolation.

### Automated Calibration (AprilTag Triangulation)

Manually finding "in focus" by eye is slow and subjective. The [`liquid-lens-calibration`](https://github.com/mpinb/liquid-lens-calibration) tool builds the same `(z, dpt)` CSV without a human judgment call: it sweeps the lens through its diopter range and uses a multi-camera Basler rig to triangulate a static AprilTag target's true `z`, then finds the best-focus diopter at each position with a focus metric computed on a XIMEA camera behind the lens.

This tool lives in a separate repository, [`liquid-lens-calibration`](https://github.com/mpinb/liquid-lens-calibration), not inside OptoFly. Clone it, point it at the reconstructor XML from step 2 above, and run it from its own project directory:

```bash
git clone https://github.com/mpinb/liquid-lens-calibration.git
cd liquid-lens-calibration
uv sync
uv run lens-calibrate --calibration /path/to/calibration_charuco.xml
```

It writes two files: a full, timestamped CSV (`calibrations/lens_calib_YYYYMMDD_HHMMSS.csv`, relative to wherever you ran it from) with every raw measurement, and — written directly into **this** repo, at the hardcoded path `/home/nfc/src/optofly/calibrations/liquid_lens.csv` — just the two columns OptoFly's loader (`setup_lens_calibration` in `src/processes/lens.py`) expects: `z` and `dpt`. No manual column-renaming step is needed. If `calibrations/liquid_lens.csv` already exists, you're prompted `replace it with this calibration? [Y/n]` — **defaults to yes**, backing up the old file in place as `calibrations/liquid_lens.csv.bak-YYYYMMDD_HHMMSS` before writing the new one. Type `n` explicitly to decline instead; that leaves the existing file untouched and saves the new calibration as `calibrations/lens_calib_YYYYMMDD_HHMMSS_optofly.csv` for you to review.

See that repo's own README for hardware setup and the full option list.

### Tuning Prediction Latency

If you enable `predictor = "linear"` (the only predictor besides `"none"`; a
`"kalman"` mode existed once and was removed, though the `[liquid_lens.kalman]`
section name was kept for config compatibility), the `system_latency` value
under `[liquid_lens.kalman]` should reflect your rig's actual measured delay,
not a guess. After running a session, measure it from the recorded lens timing CSVs:

```bash
uv run python -m src.tools.lens_latency_analyze /mnt/data/videos/<braid_dir>
```

It reads every `*_lens_timing.csv` in the folder and prints percentile breakdowns (p50/p90/p95/p99) for the USB serial write, the BraidPublisher-to-LiquidLens pubsub hop, and the end-to-end software path, plus a recommended `system_latency`.

### Troubleshooting

- **Serial port not found**: confirm the symlink with `ls -l /dev/optotune_icc1c` (or find the raw device with `ls -l /dev/ttyUSB*`); check permissions, e.g. `sudo chmod 666 /dev/ttyUSB1` on the underlying device
- **Lens not responding**: verify the handshake by running `ICC1C` interactively with `debug=True`
- **Poor focus across z-range**: add more calibration points, especially at the extremes

---

## Camera FOV Calibration

Measures the camera field-of-view at one or two z-planes so that `[camera.FOV]` (flat) or `[camera.FOV.near]` / `[camera.FOV.far]` (frustum) can be written to `config.toml`. This is the flat, one-plane version of the procedure — **use [Frustum FOV Calibration](#frustum-fov-calibration) below instead for any rig you intend to keep using**; it's more accurate for barely any extra effort. Only use the flat version below for a quick, throwaway test rig.

The z for each plane is read automatically from Braid — the tool computes the median of the Braid z values recorded during point collection, so you never have to type a height manually.

### Prerequisites

- Ximea camera connected and live
- Braid running and tracking **the laser**, not a fly: launch it with a config that uses `DetectLight` polarity (bright-object detection) instead of the `DetectDark` polarity the fly-tracking configs use — e.g. `braid-run laser.toml` if you've set one up. A fly-tracking config won't pick up a laser dot.
- Liquid lens connected with `calibrations/liquid_lens.csv` built (see Liquid Lens Calibration above)
- A laser pointer or bright LED you can hold at the frame edges
- Arena lighting **off** — both the overhead lights and the floor backlight must be off. Either light source can wash out the laser dot or be mistaken for it by the detection threshold.
- OptoFly environment activated (`uv sync`)
- You're in the OptoFly repo's own root directory (`~/src/optofly`, or wherever you cloned it) — the tool's `--config configs/config.toml` path is relative and won't resolve from anywhere else

### Procedure

1. Launch the calibration tool from the OptoFly repo root:

   ```bash
   cd ~/src/optofly   # or wherever you cloned OptoFly
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
- **`Braid connection lost: ... — retrying in 1 s`**: the tool's connection to Braid's live event stream dropped (a brief network hiccup, or Braid restarting) and it's automatically reconnecting — safe to ignore unless it keeps repeating, which means Braid itself isn't reachable. Suppressed by default; pass `--no-quiet` to see these messages if you want to confirm reconnects are actually happening.

---

## Frustum FOV Calibration

### Why This Is the Recommended Default

The flat `[camera.FOV]` calibration uses a single set of x/y bounds applied at all z heights. A camera's field of view grows with distance from the lens — the further a plane is from the camera, the wider the visible area on it. A flat FOV either misses flies at the far edge of the volume or triggers spuriously on flies outside the actual frame.

The frustum calibration captures FOV bounds at two z heights instead of one and stores both. At runtime, `TriggerHandler` linearly interpolates the bounds at the fly's actual z, giving a perspective-correct trigger zone. It costs one extra plane of data collection over the flat version, for meaningfully better accuracy — **do this one for any rig you intend to keep using.**

**`near`/`far` are z-order labels, not measured camera distance.** The calibration tools always call the lower-z plane `near` and the higher-z plane `far` — they never measure or know the camera's actual physical position, they just compare the two z values. OptoFly's camera is mounted **above** the arena, so higher z is physically *closer* to the camera and lower z is physically *farther* from it — meaning the config's `near` plane (lower z) is actually the physically far one, and `far` (higher z) is physically near. This doesn't affect correctness: the interpolation math only needs `near_z < far_z` and independently interpolates the measured bounds, so it's geometrically correct regardless of which one is physically closer to the lens. It only matters for reading your own calibration output sensibly (see the troubleshooting note below) — if your rig's camera is mounted the other way (below, looking up), the physical/config direction lines up the intuitive way and you can ignore this note.

**You need this calibration — not just the flat version — if:**
- You see flies triggering outside the visible frame (FOV too wide)
- You see flies in-frame that don't trigger (FOV too narrow)
- The discrepancy worsens at higher or lower z positions

### Prerequisites

Same as Camera FOV Calibration above.

### Procedure

`calibrate_braid_ximea.py` produces `[camera.FOV.near]` / `[camera.FOV.far]` with the same two-plane workflow used for flat FOV calibration above — after finalising plane 1, press `a` instead of `s` to add a second plane:

1. Run `calibrate_braid_ximea` and collect ≥ 4 edge points at the **near height** (lowest typical flight z). Press **`n`** to finalise.

2. Press **`a`** to add a second plane.

3. Move the laser to the **far height** (highest typical flight z). Collect ≥ 4 edge points and press **`n`**.

4. Press **`s`** to save:

   ```
   Write [camera.FOV.near] (z=0.1000 m) and [camera.FOV.far] (z=0.2500 m) to configs/config.toml? [Y/n]
   ```

   Press Enter to accept (defaults to yes). The lower-z plane is automatically assigned as `near`, higher-z as `far` (a z-order label — see the note above about what this means for a camera mounted above the arena).

5. Verify and restart:

   ```bash
   grep -A 15 "\[camera\.FOV\]" configs/config.toml
   ```

   With the camera above the arena, the *physically* farther plane — lower z, labeled `near` in the config — should have the wider bounds, and the *physically* closer plane — higher z, labeled `far` — should be narrower. (If your camera is instead mounted below looking up, it's the reverse: `far`/higher-z should be wider.) `TriggerHandler` picks the mode up from the config at startup — frustum mode is active whenever both `[camera.FOV.near]` and `[camera.FOV.far]` tables are present. Confirm what was parsed:

   ```bash
   uv run python -c "from src.utils.config import AppConfig; c = AppConfig.load('configs/config.toml'); print('frustum:', c.camera.fov_frustum, '| near z:', c.camera.fov_near_z, '| far z:', c.camera.fov_far_z)"
   ```

   Then restart the main stack.

### Troubleshooting

- **Bounds don't get wider on the plane you expected**: check which plane is physically closer to your camera, not just the `near`/`far` config labels — they only track z-order (see the note in "When to Use This"). For an overhead-mounted camera, the wider bounds should land on `near` (lower z); the narrower ones on `far` (higher z). If it's backwards from that and your camera is overhead, the laser was likely at the wrong height when you collected one of the planes — rerun the tool.

- **Trigger zone still looks wrong**: the frustum interpolates linearly. If distortion is non-linear, narrow `z_min`/`z_max` so the fly spends less time in the interpolated region.

- **`Braid connection lost` messages**: same tool as Camera FOV Calibration above — the SSE connection to Braid dropped and is auto-reconnecting; safe to ignore unless it repeats continuously.

### Output

`calibrate_braid_ximea` writes directly to `configs/config.toml`, replacing the `[camera.FOV]` section. It also stamps a `# Calibrated on <timestamp>` comment directly above `[camera.FOV]` each time it saves (refreshing the timestamp in place on a re-run rather than duplicating the comment):

```toml
# Calibrated on 2026-07-31 16:48:00
[camera.FOV]
# Frustum mode — generated by calibrate_braid_ximea.py
# To revert to flat mode, replace this block with flat x_min/x_max/y_min/y_max keys.

[camera.FOV.near]
z     = 0.1000      # z where these bounds were measured
x_min = -0.02180
x_max = 0.03900
y_min = -0.02500
y_max = 0.04100

[camera.FOV.far]
z     = 0.2500      # z where these bounds were measured
x_min = -0.01500
x_max = 0.02500
y_min = -0.01800
y_max = 0.03000
```

To revert to flat mode, replace both sub-tables with flat keys:

```toml
[camera.FOV]
x_min = -0.0218
x_max = 0.039
y_min = -0.025
y_max = 0.041
```

---

## Panda3D Heading Calibration

Screen assignment for the Panda3D pipeline comes from `screen_mapping` in `[visual_stimuli.arena]`. No interactive tool is needed there: just list which physical screen, left to right, faces which compass direction.

Aligning Braid's heading coordinate frame with the arena needs `braid_heading_offset_rad` and `braid_heading_flip`.

### Prerequisites

- Braid running and tracking your target (unless using `--standalone`). A laser pointer works well and needs a `DetectLight` config (e.g. `braid-run laser.toml`); a small white ball also works with the default `DetectDark` fly-tracking config.
- If using a laser pointer: arena lighting **off** — both the overhead lights and the floor backlight must be off, or either can wash out the laser dot or be mistaken for it.
- OptoFly environment activated (`uv sync`)

### Procedure

The first command below is all you need to perform the actual Braid-to-screen calibration; the other two are optional variants, not extra steps to run afterward:

```bash
uv run python -m src.tools.calibrate_heading                          # performs the calibration
uv run python -m src.tools.calibrate_heading --standalone              # variant: no Braid connection
uv run python -m src.tools.calibrate_heading --screens North South     # variant: calibrate a subset of screens
```

The tool shows a bright dot on each arena screen in turn. Place a Braid-trackable target (a laser pointer's dot, or a small white ball, works) directly in front of the dot and press SPACE (or Enter, if typing in the
terminal). After all screens, it fits `braid_heading_offset_rad` and `braid_heading_flip` from the Braid *position* angle of the target at each known screen direction, and offers to write both values into `visual_stimuli.toml`.

This works because Braid heading (velocity direction) and position angle share the same coordinate frame, so the same transform applies to `mean_heading` at runtime:

```
world_heading = (braid_position_angle - offset) * (-1 if flip else 1)
```
