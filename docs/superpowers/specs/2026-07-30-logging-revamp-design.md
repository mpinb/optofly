# Logging & Terminal Output Revamp

## Scope

Four fixes for terminal output:

1. **(a) Startup ordering** — three-phase startup with clear visual boundaries
2. **(c) Noise reduction** — suppress known third-party noise sources
3. **(b) Format unification** — all output through logger; per-handler levels (console=INFO, file=DEBUG)
4. **(d) Per-trial grouping** — compact trial summary rows (one per stimulus), detail in file only

Also: sync `config.example.toml` / `visual_stimuli.example.toml` to current defaults.
Also: stop spawning the monitoring server.

---

## 1. Startup Phase Coordination

### Design

Three phases delimited by output ordering:

**Phase 1 — Pre-init** (main.py, before any process spawn):
- Config loading, Braid connection, folder creation, metadata skip
- All through `logger.info()`, not `print()`
- Ends with `logger.info("Starting experiment...")`

**Phase 2 — Process initialization** (Experiment.start):
- Experiment Configuration summary block prints at the *start* of `start()`, before spawning any process
- Each process spawns (0.5s stagger) and logs init via standard structured logger
- Third-party noise from Panda3D/xiAPI suppressed (see §2)
- Liveness checked before returning to main.py

**Phase 3 — Ready**:
- `logger.info("All systems ready. Ctrl+C to stop.")`
- Enter run loop

No new ZMQ channels; gating from output ordering alone.

### Files changed
- `main.py`: convert `print()` to `logger.info()`; move config-block call into `Experiment.start()`
- `src/orchestration.py`: absorb Experiment Configuration as `logger.info()` at start of `start()`

---

## 2. Noise Suppression

| Source | File | Fix |
|---|---|---|
| Panda3D "Known pipe types" | `src/visual/scene.py` | `redirect_stdout(None)` + `redirect_stderr(None)` around `ShowBase.__init__()` |
| xiAPI bandwidth/alloc | `src/processes/camera.py` | String-match blocklist in `_forward_output()`: suppress `EAL_IF`, `FGTL_SetParam_to_CAL`, `SAL_Common_SetAcquisitionFrameRate`, `AllocateBuffers`, `Bandwidth measurement` |
| SSE gap at startup | `src/processes/braid.py` | Skip gap warnings before first Update (`_first_update_seen` flag) |
| Duplicate lines | `main.py` | Remove "Experiment duration" and "Logging to" duplicates |
| Monitoring server | `src/orchestration.py` | Do not spawn regardless of config |

---

## 3. Format Unification

### 3a. print() → logger

`main.py` gets a module-level `logger`. All `print()` becomes `logger.info()`. Experiment Config block becomes `logger.info()` in `orchestration.py`.

### 3b. Per-handler log levels

`configure_process_logging()` gets two new parameters:

```python
def configure_process_logging(
    log_path, process_name, color=None,
    level=logging.INFO,
    console_level=logging.INFO,
    file_level=logging.DEBUG,
):
    stream_handler.setLevel(console_level)
    file_handler.setLevel(file_level)
    root.setLevel(min(console_level, file_level))
```

Console shows INFO and above (startup, health, shutdown, trial summary rows).
File (`optofly.log`) captures DEBUG and above (all detail).

Backward compatible: defaults preserve current behavior.

### 3c. Trial context on log lines

Each process tracks `_trial_count` and `_current_trial_obj`. Trial log lines prefixed `[#N obj=M]` in the message field. These go to the file at whatever level is appropriate (INFO for milestone events, DEBUG for per-frame detail).

---

## 4. Per-Trial Grouping

### Console output (print, no logger prefix)

```
── Trial #1 obj=51 ──
  opto:    red  128/255  300ms  real   frame=104106
  visual:  looming  pos=0°  type=exponential  frame=104106
── Trial #1 end (0.23s) ──

── Trial #2 obj=53 ──
  opto:    red  128/255  300ms  sham   frame=105156
  visual:  looming  pos=0°  type=exponential  frame=105156
── Trial #2 end (0.13s) ──
```

- Banners and stimulus rows use `print()` — no structured prefix, for a clean summary
- `opto:` row emitted by `OptoTriggerWorker` after trigger fires
- `visual:` row emitted by `VisualProcess` after trigger fires (one row per active stimulus type)
- Banners emitted by `TriggerHandler` on ZONE_ENTER / ZONE_EXIT
- The `end` banner includes total zone-occupancy duration
- Multiple visual stimuli each get their own `visual:` row

### File output (optofly.log)

All the detail formerly at INFO level moves to structured log lines:

- ZONE_ENTER details → `logger.info()` in TriggerHandler (INFO — key milestone)
- ZONE_EXIT details → `logger.info()` in TriggerHandler
- Lens tracking start/stop → `logger.info()`
- Per-frame lens focus updates → `logger.debug()`
- Lens timing CSV stats → `logger.debug()`
- Camera recording frames/dropped → `logger.info()`
- VISUAL_ZONE_ENTER world_heading → `logger.debug()`

### Shutdown banners

Same `print()` style from main.py:
```
── Shutting down (Ctrl+C) ──
── Experiment ended. Data: /mnt/data/experiments/... ──
```

---

## 5. Config Example Sync

```bash
cp configs/config.toml configs/config.example.toml
cp configs/visual_stimuli.toml configs/visual_stimuli.example.toml
```

---

## 6. Files Changed

| File | Changes |
|---|---|
| `main.py` | Module logger; `print()`→`logger.info()`; remove duplicates; remove config block call, add phase-3 ready line; shutdown banners |
| `src/orchestration.py` | Absorb config block; skip monitoring spawn |
| `src/utils/logger.py` | Add `console_level` + `file_level` params to `configure_process_logging()` |
| `src/utils/worker.py` | Pass through new params; bump default `self.log_level` to `"DEBUG"` (file) |
| `src/visual/scene.py` | `redirect_stdout`/`redirect_stderr` around `ShowBase.__init__()` |
| `src/processes/camera.py` | Noise blocklist in `_forward_output()`; trial counter + context prefix |
| `src/processes/braid.py` | Suppress SSE gap before first Update |
| `src/processes/tracking.py` | Trial banners (`print`); trial counter + context prefix; move detail to appropriate level |
| `src/processes/lens.py` | Trial counter; move per-frame detail to DEBUG; `print` column headers only once |
| `src/processes/led.py` | Emit `opto:` summary `print()` row; trial counter; move detail to DEBUG |
| `src/visual/process.py` | Emit `visual:` summary `print()` row; trial counter; move detail to DEBUG |

### Files not changed

- `src/utils/metadata.py` — interactive prompts use `print()` by design
- `src/tools/` — standalone, not part of experiment run
- `optofly-camera/` — Rust binary unchanged; noise filtered in Python wrapper
- `src/monitoring/` — left as-is; just not spawned

---

## 7. Expected Output

```
[2026-07-30 14:12:51 - Main - __main__] INFO: Loading configuration from configs/config.toml
[2026-07-30 14:12:51 - Main - __main__] INFO: Starting Braid recording...
[2026-07-30 14:12:51 - Main - __main__] INFO: Braid folder: /mnt/data/experiments/20260730_141251.braid
[2026-07-30 14:12:51 - Main - __main__] INFO: Skipping metadata (--skip-metadata)
[2026-07-30 14:12:51 - Main - src.orchestration] INFO: Logging to: /mnt/data/experiments/20260730_141251.braid/optofly.log

======================================================================
OptoFly Experiment Configuration
======================================================================
Active Processes:
  ✓ BraidPublisher
  ✓ TriggerHandler
  ✓ LatencyLogger
  ✓ VisualProcess
  ✓ CameraProcess
  ✓ LiquidLens
  ✓ OptoTriggerWorker

Visual Stimuli: Background, Looming (type=exponential)
Opto: red, 128/255, 300ms
Camera: 2112x2112 @ 500fps
Lens: diopter, linear predictor (latency=0.01s, horizon=0.01s)
======================================================================

[2026-07-30 14:12:51 - Main - src.orchestration] INFO: Starting core processes...
[2026-07-30 14:12:52 - BraidPublisher - src.processes.braid] INFO: Connected to Braid at http://127.0.0.1:8397/events
[2026-07-30 14:12:52 - BraidPublisher - src.processes.braid] INFO: ZMQ publisher bound to BRAID:5555, ACTIVE_BRAID:5557
[2026-07-30 14:12:52 - TriggerHandler - src.processes.tracking] INFO: Initialized (cooldown=10.0s)
[2026-07-30 14:12:53 - Main - src.orchestration] INFO: Starting optional processes...
[2026-07-30 14:12:53 - VisualProcess - src.visual.process] INFO: Panda3D window: 7680x1080, 4 screens (South→West→North→East)
[2026-07-30 14:12:53 - VisualProcess - src.visual.process] INFO: Braid calibration: offset=1.547 rad flip=True
[2026-07-30 14:12:53 - LiquidLens - src.processes.lens] INFO: Connected to ICC-1C EL-16-40-TC-5D (linear predictor)
[2026-07-30 14:12:54 - VisualProcess - src.visual.process] INFO: Registered: BackgroundStimulus, LoomingStimulus
[2026-07-30 14:12:55 - OptoTriggerWorker - src.processes.led] INFO: Connected to Arduino at /dev/opto_trigger
[2026-07-30 14:12:59 - RustCamera - src.processes.camera] INFO: Camera opened. Model:CB160MG-LX-X8G3 SN:BLMID2407000
[2026-07-30 14:13:01 - Main - __main__] INFO: All systems ready. Ctrl+C to stop.

── Trial #1 obj=51 ──
  opto:    red  128/255  300ms  real   frame=104106
  visual:  looming  pos=0°  type=exponential  frame=104106
── Trial #1 end (0.23s) ──

── Trial #2 obj=53 ──
  opto:    red  128/255  300ms  sham   frame=105156
  visual:  looming  pos=0°  type=exponential  frame=105156
── Trial #2 end (0.13s) ──

── Trial #3 obj=53 ──
  opto:    red  128/255  300ms  real   frame=106207
  visual:  looming  pos=0°  type=exponential  frame=106207
── Trial #3 end (0.15s) ──

── Trial #4 obj=53 ──
  opto:    red  128/255  300ms  real   frame=107253
  visual:  looming  pos=0°  type=exponential  frame=107253
── Trial #4 end (0.18s) ──

^C
── Shutting down (Ctrl+C) ──
[2026-07-30 14:14:01 - TriggerHandler - src.processes.tracking] INFO: Stopping (4 triggers this run)
[2026-07-30 14:14:01 - Main - src.orchestration] INFO: Waiting for processes to terminate...
[2026-07-30 14:14:09 - Main - src.orchestration] INFO: ✓ Recording stopped
[2026-07-30 14:14:09 - Main - __main__] INFO: Found 6 CSV files in /mnt/data/experiments/20260730_141251.braid
── Experiment ended. Data: /mnt/data/experiments/20260730_141251.braid ──
```
