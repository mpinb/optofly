# Branch Status Summary
Generated: 2026-05-01

---

## `main`

Current state: includes `feat/braid-camera-calibration` (merged), `refactor/kalman-1d` (merged), and `feature/lens-latency` / `feature/pre-trigger-zone` / `feature/rust-ximea-camera` / `fix/liquid-lens-logging` (all merged, stale remotes).

---

## `feat/lens-sine-sweep`

**Status: Active — 6 commits ahead of main, clean, ready for review/merge**

### What changed

Adds a new `"sweep"` autofocus mode to the liquid lens. Instead of issuing a DC diopter
command on every BRAID update, the lens free-runs a 25 Hz sine oscillation. A background
thread in `CameraProcess` measures per-frame sharpness (Laplacian variance) across each
sweep cycle and publishes the best-focus diopter over ZMQ. The lens process receives this
and shifts the oscillation center accordingly.

The sweep only runs while a fly is actively in the camera FOV — the lens starts
oscillating on ZONE_ENTER and parks back to DC mode on ZONE_EXIT, avoiding unnecessary
actuator wear between trials.

| File | Change |
|------|--------|
| `src/hardware/lens.py` | `to_sine_mode()`, `update_sine_center()`, `dpt_to_ma()`, `_xi()` — hardware methods to configure and update the Optotune sine sweep over serial |
| `src/processes/lens.py` | `"sweep"` predictor branch; `_start_sweep(z)` called on ZONE_ENTER; `to_focal_power_mode()` called in `_stop_tracking()`; DFF socket subscription; `_update_sweep_center()` and `_drain_dff_socket()` (gated on `is_tracking` and `obj_id`) |
| `src/processes/camera.py` | `sweep_t0` / `sweep_center_dpt` shared-memory args; `_dff_worker()` background thread; BRAID subscriber for fly position → Ximea ROI centering via `BraidToXimeaCalibration` |
| `src/utils/dff.py` | **New.** `laplacian_sharpness()` and `parabolic_peak()` |
| `src/utils/config.py` | Sweep params in `LiquidLensConfig`; `dff_port` in `ZMQConfig`; DFF + `braid_ximea_calibration_file` fields in `CameraConfig` |
| `main.py` | Creates `mp.Value` shared state (`sweep_t0`, `sweep_center_dpt`); passes to both `CameraProcess` and `LiquidLens` |
| `configs/config.example.toml` | `dff_port`, `[camera.dff]`, `[liquid_lens.sweep]` sections |

### Sweep lifecycle

```
Startup         → DC focal-power mode (lens idle)
ZONE_ENTER      → _start_sweep(entry_z): calibration lookup → to_sine_mode()
During trial    → BRAID updates shift center (threshold-gated)
                  DFF_PEAK messages (this obj_id only) refine center
ZONE_EXIT       → to_focal_power_mode() (lens parked)
```

### Config additions needed in `config.toml`

```toml
[zmq]
dff_port = 5557

[camera.dff]
enabled       = false   # true to activate DFF feedback
roi_size_px   = 64
freq_hz       = 25.0    # must match liquid_lens.sweep.freq_hz
amplitude_dpt = 0.2     # must match liquid_lens.sweep.amplitude_dpt

# optional — enables ROI centering on fly pixel position:
# braid_ximea_calibration_file = "calibrations/braid_to_ximea.npz"

[liquid_lens]
predictor = "sweep"

[liquid_lens.sweep]
freq_hz                 = 25.0
amplitude_dpt           = 0.2
default_center_dpt      = 5.0
center_update_threshold = 0.05
```

### Merge difficulty: Medium

Touches 7 files. No conflicts with current main. Before merging:
- Hardware smoke test of `to_sine_mode` / `update_sine_center` with Optotune connected
- Full system trial with `predictor = "sweep"` and `dff_enabled = true` to verify
  DFF_PEAK messages flow and sweep center updates

---

## `feature/braid-sse-stability`

**Status: Active — 2 commits ahead of main, not yet merged**

Two independent fixes to the BRAID SSE pipeline:

**Commit 1 — line-driven SSE parser** (`fix(braid): line-driven SSE parser to stop dropping coalesced events`):
The old `parse_chunk()` split on `\n\n` and expected exactly 2 lines per chunk. When the
HTTP stack coalesces multiple events into one `iter_content` chunk (common under load),
every event after the first was silently dropped. Replaced with a proper generator
`iter_sse_events()` that processes line-by-line, buffers partial events, and emits one
`(event_type, data)` tuple per blank-line boundary. Also adds a wall-clock gap warning
(> 25 ms between events flags an upstream stall) and a `HTTPAdapter` with retry logic.

**Commit 2 — active BRAID lens feed** (`feat: add active braid lens feed`):
Mirrors the `feature/active-object-relay` concept but implemented inline in
`BraidPublisher` + `LiquidLens` rather than as a separate relay process. `LiquidLens`
connects to `active_braid_port` and subscribes to `active_braid_topic`; uses
`zmq.CONFLATE` + `RCVHWM=1` so the lens always sees the freshest update. Adds a
`_pending_first_update` buffer for the first BRAID message arriving before ZONE_ENTER.

| File | Change |
|------|--------|
| `src/processes/braid.py` | `iter_sse_events()` replaces `parse_chunk()`; `HTTPAdapter` with retries; event-boundary gap warning |
| `src/processes/lens.py` | Subscribes to `active_braid_port`/`active_braid_topic`; `CONFLATE` socket option; `_pending_first_update` |
| `src/processes/tracking.py` | Minor — test-driven cleanup |
| `src/utils/config.py` | `active_braid_port`, `active_braid_topic`, `lens_update_conflate` in `ZMQConfig` |
| `configs/config.example.toml` | New ZMQ keys |
| `tests/test_braid_sse.py` | **New.** Tests for `iter_sse_events()` |
| `tests/test_braid_publisher_active.py` | **New.** Tests for active-feed publishing |
| `tests/test_lens.py` / `tests/test_trigger_handler.py` | Updated |

### Merge difficulty: Medium

The SSE parser fix (commit 1) is a clean, high-value bug fix and should be merged
independently. The active-feed commit (commit 2) overlaps with
`feature/active-object-relay` — both solve the same problem (filtered BRAID → lens)
with different architectures. Decide which approach to keep before merging either.

---

## `frustum-trigger-zone`

**Status: Active — 1 commit ahead of main, not yet merged**

Adds a perspective-correct frustum trigger zone to `TriggerHandler`. Instead of using a
fixed rectangular FOV box at all heights, the frustum mode linearly interpolates the
x/y bounds between a near plane (low z) and a far plane (high z), matching the actual
camera viewing cone. Useful when the lens FOV is meaningfully narrower near the fly
than far.

The existing flat-box behavior is unchanged when `fov_frustum = false` (default). The
new `_get_fov_at_z(z)` helper does the interpolation; `is_in_trigger_zone()` is updated
to call it.

| File | Change |
|------|--------|
| `src/processes/tracking.py` | `_get_fov_at_z(z)` helper; updated `is_in_trigger_zone()`; frustum params loaded at init |
| `src/utils/config.py` | `fov_frustum`, `fov_near_z`, `fov_near_x_min/max`, `fov_near_y_min/max`, `fov_far_z`, `fov_far_x_min/max`, `fov_far_y_min/max` added to `TriggerHandlerConfig` |
| `configs/config.example.toml` | `[camera.FOV.frustum]` or equivalent section |
| `tests/test_trigger_handler.py` | Frustum zone tests |

### Config additions needed in `config.toml`

```toml
[trigger_handler]
fov_frustum = true

[trigger_handler.frustum]
near_z     = 0.10   # metres — bottom of z range
near_x_min = -0.015
near_x_max = 0.030
near_y_min = -0.015
near_y_max = 0.030
far_z      = 0.25   # metres — top of z range
far_x_min  = -0.025
far_x_max  = 0.045
far_y_min  = -0.025
far_y_max  = 0.045
```

### Merge difficulty: Low

Single commit, clean. No conflicts with main. Opt-in via `fov_frustum = false` default
so existing configs are unaffected. Only pre-merge work is measuring the actual frustum
bounds from the calibration file and populating config values.

---

## Merged branches (on main)

### `feat/braid-camera-calibration` ✅ merged
Interactive DLT calibration tool (`src/tools/calibrate_braid_ximea.py`) mapping BRAID
world coords → Ximea pixel coords. Also computes `[camera.FOV]` automatically.
Class: `BraidToXimeaCalibration` (`src/utils/calibration.py`).
Config key: `braid_ximea_calibration_file`.

### `refactor/kalman-1d` ✅ merged
Replaced 6D `[x,y,z,vx,vy,vz]` Kalman filter with a 2-state `[z, vz]` filter.
The lens only uses predicted z; x/y were dead weight. `predict()` now returns `float`
directly. `src/utils/kalman_filter.py` shrank from 558 → 130 lines.

### `feature/lens-latency` ✅ merged
Lens latency measurement tooling.

### `feature/pre-trigger-zone` ✅ merged
Pre-trigger zone detection logic.

### `feature/rust-ximea-camera` ✅ merged
Rust-based XIMEA camera process (`optofly-camera/`).

### `fix/liquid-lens-logging` ✅ merged
Liquid lens logging fix.

### `feature/active-object-relay` 🗑 deleted
Dropped — not needed. The `obj_id` filtering is handled inline in `LiquidLens`.
