# Branch Status Summary
Generated: 2026-05-01

---

## `refactor/kalman-1d`

**Status: Active — ready to merge**

### What changed
Replaces the original 6-dimensional Kalman filter `[x, y, z, vx, vy, vz]` with a
focused 2-state filter `[z, vz]`. The liquid lens only ever used `predicted[2]` (the
z component) — x and y were dead weight. In the DWNA motion model those three axes
are fully decoupled, so the 6D filter was literally three independent 2-state filters
running in parallel at no benefit.

| File | Change |
|------|--------|
| `src/utils/kalman_filter.py` | Complete rewrite: 558 → 130 lines. Class is now 2×2 matrices; `predict()` returns `float` instead of `(x,y,z)` tuple. Numba kernels kept unchanged (generic over shape). |
| `src/processes/lens.py` | Updated 5 lines: pass `z, vz` scalars instead of `(x,y,z)` tuples; drop `predicted[2]` indexing. |
| `configs/config.example.toml` | Comment updated: "6-state" → "2-state [z, vz]". |

### Merge difficulty: Easy
Self-contained. No conflicts with `feat/braid-camera-calibration` (they only share
`config.example.toml`, touching different lines).

**Caveat for `feat/lens-sine-sweep`:** that branch was written against the old 6D
interface (`kalman.init((x,y,z), ...)`, `predicted[2]`). If `refactor/kalman-1d` is
merged first, the sweep branch will need ~5 lines updated in its predictor block before
it can merge cleanly.

---

## `feat/braid-camera-calibration`

**Status: Active — ready to merge**

### What changed
Adds an interactive DLT-based calibration tool that maps BRAID 3D world coordinates
`(x, y, z)` to camera pixel positions `(u, v)`. Replaces the manual laser-pointer
workflow for measuring the camera field of view.

| File | Change |
|------|--------|
| `src/utils/calibration.py` | **New.** `BraidToCameraCalibration` class: `fit` (SVD-based DLT), `project`, `backproject` (at known z), `compute_fov` (back-projects 4 frame corners → FOV bounds), `save`/`load`. |
| `src/tools/calibrate_braid_camera.py` | **New.** 537-line interactive CLI: live BRAID position feed via ZMQ, OpenCV window with mouse callbacks, running counter (corners vs interior points), orange FOV overlay (`v` key), interactive z_ref prompt, in-session y/N offer to write `[camera.FOV]` to config. |
| `src/utils/config.py` | +4 lines: `CameraConfig.braid_camera_calibration_file` optional field. |
| `configs/config.example.toml` | Commented-out `braid_camera_calibration_file` entry; FOV comment explaining the tool. |
| `docs/calibration.md` | +123 lines: full BRAID-to-camera section (procedure, key bindings, FOV computation, theory, output format, troubleshooting). |
| `docs/setup.md` | +17 lines: calibration tools section with commands. |
| `README.md` | Updated calibration.md description. |

### Merge difficulty: Very easy
All additive. No functional changes to existing code. The only shared file with other
branches is `config.example.toml` (different sections, no line-level conflict).

---

## `feat/lens-sine-sweep`

**Status: Active but incomplete — has a pending stash**

### What changed
Implements a continuous sine sweep mode for the liquid lens plus a depth-from-focus
(DFF) feedback loop. Instead of issuing DC diopter commands on each BRAID update, the
lens free-runs a 25 Hz sine oscillation while a background thread computes per-frame
sharpness (Laplacian variance) and publishes the best-focus diopter over ZMQ.

| File | Change |
|------|--------|
| `src/hardware/lens.py` | +61 lines: `dpt_to_ma()`, `_xi()`, `to_sine_mode()`, `update_sine_center()` — hardware methods to configure and update the Optotune sine sweep over serial. |
| `src/processes/lens.py` | +136 lines: new `"sweep"` predictor branch; `sweep_t0` and `sweep_center_dpt` shared-memory args; DFF socket subscription; `_update_sweep_center()` and `_drain_dff_socket()` methods; lens parked back to DC on close. |
| `src/processes/camera.py` | +137 lines: `CameraProcess` now accepts `sweep_t0` / `sweep_center_dpt` `mp.Value` args; `_dff_worker()` background thread reads ROI from frame queue, computes sharpness, publishes `DFF_PEAK` ZMQ messages. |
| `src/utils/dff.py` | **New.** `laplacian_sharpness()` and `parabolic_peak()` utilities. |
| `src/utils/config.py` | +30 lines: `LiquidLensConfig` sweep params; `ZMQConfig.dff_port`; `CameraConfig` DFF fields. |
| `main.py` | +19 lines: creates `mp.Value` shared state (`sweep_t0`, `sweep_center_dpt`); passes them to both `CameraProcess` and `LiquidLens`. |
| `configs/config.example.toml` | +18 lines: `dff_port`, `[camera.dff]` section, `[liquid_lens.sweep]` section. |

### Pending stash (`stash@{0}`)
The stash contains ~500 lines of uncommitted documentation updates across:
`README.md`, `docs/architecture.md`, `docs/calibration.md`, `docs/camera.md`,
`docs/setup.md`, plus small additions to `src/processes/camera.py` and
`src/utils/config.py`. This work documents the sweep/DFF feature but was never
committed to the branch. It needs to be reviewed and either committed or dropped
before the branch is in a mergeable state.

### Merge difficulty: Medium
The code itself is well-structured. The friction points are:

1. **Stash must be resolved first.** Pop the stash, review, and commit what's worth
   keeping. The `src/processes/camera.py` and `src/utils/config.py` changes in the
   stash may conflict with the committed code on the branch.

2. **Conflict with `refactor/kalman-1d` in `lens.py`** (if kalman-1d merges first):
   the sweep branch uses the old tuple-based Kalman interface (`init((x,y,z), ...)`,
   `predicted[2]`). About 5 lines need updating to the scalar interface.

3. **Conflict with `feat/braid-camera-calibration` in `config.py`** (if calib merges
   first): both add fields to `CameraConfig.__init__` near the same location.
   Straightforward to resolve manually (~3 lines).

4. **Hardware-only validation.** The `to_sine_mode` / `update_sine_center` serial
   commands can only be verified with an Optotune lens connected. No unit tests exist
   for the new lens hardware methods.

---

## Recommended Merge Order

```
main
  ↓  1. refactor/kalman-1d          (clean, no deps, small)
  ↓  2. feat/braid-camera-calibration (clean, no deps, additive)
  ↓  3. feat/lens-sine-sweep         (after resolving stash; update 5 kalman lines)
```

Steps 1 and 2 can be swapped — they do not conflict with each other.

Before merging `feat/lens-sine-sweep`:
- `git stash pop` on that branch, review, commit docs
- If `refactor/kalman-1d` is already on main: update `lens.py` lines ~357–371
  (`kalman.init` / `kalman.update` / `kalman.predict` calls) to scalar interface
- If `feat/braid-camera-calibration` is already on main: resolve the `CameraConfig`
  addition near line 420 of `config.py` (mechanical, no logic conflict)
