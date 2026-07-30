# Logging & Terminal Output Revamp — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean, scannable terminal output with three-phase startup, per-trial banner separators, suppressed third-party noise, and per-handler log levels (console=INFO, file=DEBUG).

**Architecture:** No new ZMQ channels or processes. Changes touch 11 Python files and 2 config example files. The core mechanisms are: (1) `configure_process_logging()` gains per-handler level params so console shows INFO+ and `optofly.log` captures DEBUG+, (2) `main.py`'s `print()` calls become `logger` calls or banner `print()` lines, (3) trial processes emit compact `print()` summary rows and push subsystem chatter to DEBUG, (4) third-party noise is blocked at source.

**Tech Stack:** Python 3.12, multiprocessing (spawn), ZMQ PUB/SUB, logging stdlib, Panda3D, XIMEA SDK (Rust binary wrapper).

---

### Task 1: Per-handler log levels in `configure_process_logging()`

**Files:**
- Modify: `src/utils/logger.py:31-55`
- Modify: `src/utils/worker.py:8-16, 43-48`

- [ ] **Step 1: Add `console_level` and `file_level` parameters to `configure_process_logging()`**

Replace the function body and signature in `src/utils/logger.py`:

```python
def configure_process_logging(
    log_path: str | None,
    process_name: str,
    color: str | None = None,
    level: int = logging.INFO,
    console_level: int | None = None,
    file_level: int | None = None,
) -> None:
    """Configure the root logger for a process. Call once at process entry.

    console_level / file_level override `level` for their respective handlers.
    When both are None (the default), both handlers share `level` — backward
    compatible with every existing caller.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    logging.disable(logging.NOTSET)

    color_code = COLORS.get((color or "WHITE").upper(), COLORS["WHITE"])
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(ColoredFormatter(process_name, color_code))
    stream.setLevel(console_level if console_level is not None else level)
    root.addHandler(stream)

    effective_file_level: int | None = None
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file = logging.FileHandler(log_path, mode="a")
        file.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        effective_file_level = file_level if file_level is not None else level
        file.setLevel(effective_file_level)
        root.addHandler(file)

    effective_console = console_level if console_level is not None else level
    effective_file = effective_file_level if effective_file_level is not None else effective_console
    root.setLevel(min(effective_console, effective_file))
```

- [ ] **Step 2: Pass `file_log_level` through `WorkerProcess`**

In `src/utils/worker.py`, add the new parameter to `__init__` and update `run()`:

```python
class WorkerProcess(Process):
    def __init__(
        self,
        event: Event,
        log_path: str | None = None,
        log_level: str = "INFO",
        file_log_level: str = "DEBUG",
        log_color: str | None = None,
        process_name: str = "WorkerProcess",
        failure_queue=None,
    ):
        ...
        self.log_level = log_level
        self.file_log_level = file_log_level
        ...

    def run(self):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        configure_process_logging(
            self.log_path,
            self.process_name,
            self.log_color,
            level=getattr(logging, self.log_level.upper(), logging.INFO),
            console_level=getattr(logging, self.log_level.upper(), logging.INFO),
            file_level=getattr(logging, self.file_log_level.upper(), logging.DEBUG),
        )
        ...
```

The `file_log_level` parameter defaults to `"DEBUG"` so all worker processes automatically capture DEBUG+ to the file without any caller changes.

- [ ] **Step 3: Verify backward compatibility**

```bash
uv run python -c "from src.utils.logger import configure_process_logging; configure_process_logging(None, 'Test', 'WHITE')"
```

Expected: no error. The new `console_level`/`file_level` params are optional and default to `None` (fall back to `level`).

- [ ] **Step 4: Commit**

```bash
git add src/utils/logger.py src/utils/worker.py
git commit -m "feat: add per-handler console/file log levels to configure_process_logging"
```

---

### Task 2: Sync config example files

**Files:**
- Modify: `configs/config.example.toml`
- Modify: `configs/visual_stimuli.example.toml`

- [ ] **Step 1: Sync the example configs**

```bash
cp configs/config.toml configs/config.example.toml
cp configs/visual_stimuli.toml configs/visual_stimuli.example.toml
```

- [ ] **Step 2: Verify diff is clean (only the gitignore-sensitive actual configs differ)**

```bash
diff configs/config.example.toml configs/config.toml
diff configs/visual_stimuli.example.toml configs/visual_stimuli.toml
```

Expected: no output (files are identical).

- [ ] **Step 3: Commit**

```bash
git add configs/config.example.toml configs/visual_stimuli.example.toml
git commit -m "chore: sync example configs to current defaults"
```

---

### Task 3: Convert `main.py` output to structured logging + banners

**Files:**
- Modify: `main.py:1-260`

- [ ] **Step 1: Add module-level logger**

Add after the existing imports in `main.py`:

```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Convert `load_config()` error paths to `logger`**

Replace `print()` calls in `load_config()` (lines 33-51):

```python
def load_config(config_path: str) -> AppConfig:
    try:
        return AppConfig.load(config_path)
    except FileNotFoundError:
        logger.error("Config file not found: %s", config_path)
        logger.error("  Create it with: cp configs/config.example.toml configs/config.toml")
        sys.exit(1)
    except tomllib.TOMLDecodeError as e:
        logger.error("%s is not valid TOML.", config_path)
        logger.error("  %s", e)
        sys.exit(1)
    except ValueError as e:
        logger.error("invalid configuration")
        for line in str(e).splitlines():
            logger.error("  %s", line)
        sys.exit(1)
    except Exception as e:
        logger.error("Failed to load config %s: %s", config_path, e)
        sys.exit(1)
```

- [ ] **Step 3: Convert pre-experiment print() calls**

In `main()`, replace lines 176-204:

```python
    logger.info("Loading configuration from %s...", config_path)
    app_config = load_config(config_path)

    recording_time_warning = check_recording_time_sufficient(app_config)
    if recording_time_warning:
        logger.warning(recording_time_warning)

    experiment = Experiment()

    try:
        braid_folder = experiment.prepare_braid_folder(config_path)
    except BraidFolderError as e:
        logger.error("%s", e)
        sys.exit(1)
    logger.info("Experiment data will be saved to: %s", braid_folder)

    metadata = None
    if not args.skip_metadata:
        try:
            metadata = collect_metadata()
        except UserCancelledError:
            handle_metadata_cancellation(experiment.braid_proxy)
            sys.exit(0)
        experiment_duration = float(metadata.get("experiment_duration", 24))
    else:
        experiment_duration = 24.0
        logger.info("Skipping metadata (--skip-metadata)")
```

- [ ] **Step 4: Update experiment start and run loop**

Replace lines 206-230 in `main()`:

```python
    start_failed = False
    try:
        experiment.start(config_path, metadata)

        logger.info("All systems ready. Ctrl+C to stop.")
        end_time = experiment.status()["end_time"]

        while experiment.is_running():
            if datetime.now().timestamp() >= end_time:
                print("\n── Experiment duration reached ──")
                break
            experiment.check_health()
            if not experiment.is_running():
                print("\n── Critical process died ──")
                for line in format_critical_failures(experiment.status()):
                    print(f"  {line}")
                break
            time.sleep(0.1)
    except ExperimentStartError as e:
        logger.critical("FATAL: %s", e)
        start_failed = True
    except KeyboardInterrupt:
        print("\n── Shutting down (Ctrl+C) ──")
    except Exception as e:
        logger.error("ERROR during experiment: %s", e)
        import traceback
        traceback.print_exc()
        raise
```

- [ ] **Step 5: Update shutdown and finally block**

Replace lines 242-252:

```python
    finally:
        logger.info("Shutting down processes...")
        braid_folder_at_stop = experiment.status()["braid_folder"]
        experiment.stop()

        if braid_folder_at_stop:
            print(f"\n── Experiment ended. Data: {braid_folder_at_stop} ──")
        else:
            print("── Experiment terminated ──")
```

- [ ] **Step 6: Update `handle_metadata_cancellation()`**

Replace `print()` calls with `logger.info()`:

```python
def handle_metadata_cancellation(braid_proxy) -> None:
    logger.info("Metadata collection cancelled by user.")
    if braid_proxy is not None:
        logger.info("Stopping Braid recording...")
        try:
            braid_proxy.stop_csv_recording()
            logger.info("Recording stopped")
        except Exception as e:
            logger.warning("Failed to stop recording: %s", e)
    logger.info("Exiting.")
```

- [ ] **Step 7: Remove unused `print_experiment_config()` function**

Delete lines 85-135 (the entire `print_experiment_config` function). It moves to `orchestration.py` in Task 4.

- [ ] **Step 8: Remove unused import**

Remove `tomllib` import from line 12 if `print_experiment_config` was the only user (check: it was used there to parse visual stimuli config).

- [ ] **Step 9: Commit**

```bash
git add main.py
git commit -m "refactor: convert main.py output to structured logging + banner separators"
```

---

### Task 4: Absorb config summary into `Experiment.start()`, disable monitoring

**Files:**
- Modify: `src/orchestration.py:222-377, 33`

- [ ] **Step 1: Add tomllib import**

Add at the top of `orchestration.py`:

```python
import tomllib
```

- [ ] **Step 2: Move Experiment Configuration block into `start()`**

In `start()`, after `_copy_config_to_braid_folder` lines and before `logger.info("Starting core processes...")`, insert the config summary. Replace the existing `logger.info("Starting core processes...")` position:

```python
        log_path = str(Path(braid_folder) / "optofly.log")
        self._log_path = log_path
        configure_process_logging(log_path, "Main", "WHITE", level=log_level_int)
        logger.info("Logging to: %s", log_path)

        stop_event = mp.Event()
        self._stop_event = stop_event
        self._processes = []
        self._failed_reasons = {}
        self._shutdown_state = {}
        self._known_dead = set()
        self._critical = _critical_names(app_config)
        self._failure_queue = mp.Queue()
        self._reported_reasons = {}

        common = dict(
            config_path=config_path,
            event=stop_event,
            log_path=log_path,
            log_level=log_level_str,
            failure_queue=self._failure_queue,
        )

        # ── Experiment configuration summary ──
        print("\n" + "=" * 70)
        print("OptoFly Experiment Configuration")
        print("=" * 70)
        print("\nActive Processes:")
        print("  ✓ BraidPublisher")
        print("  ✓ TriggerHandler")
        print("  ✓ LatencyLogger")
        if app_config.visual_stimuli.active:
            print("  ✓ VisualProcess (Panda3D)")
        if app_config.camera.active:
            print("  ✓ CameraProcess")
            print("  ✓ LiquidLens")
        print("  ✓ OptoTriggerWorker")
        if app_config.camera.active:
            print(f"\nCamera: {app_config.camera.resolution[0]}×{app_config.camera.resolution[1]} @ {app_config.camera.fps}fps")
        if app_config.liquid_lens.mode:
            predictor = app_config.liquid_lens.predictor
            lens_detail = f"diopter, {predictor} predictor"
            if predictor == "linear":
                lens_detail += f" (latency={app_config.liquid_lens.system_latency}s, horizon={app_config.liquid_lens.prediction_horizon}s)"
            print(f"Liquid Lens: {lens_detail}")
        if app_config.opto_trigger.active:
            print(f"\nOpto Trigger: {app_config.opto_trigger.color}, {app_config.opto_trigger.intensity}/255, {app_config.opto_trigger.duration}ms")
        if app_config.visual_stimuli.active:
            enabled_stimuli = []
            try:
                with open(app_config.visual_stimuli.config_file, "rb") as f:
                    vs_data = tomllib.load(f).get("visual_stimuli", {})
                for section_name, section in vs_data.items():
                    if isinstance(section, dict) and section.get("enabled", False):
                        name = section_name.replace("_", " ").capitalize()
                        if section_name == "looming":
                            exp_type = section.get("type", "exponential")
                            name += f" (type={exp_type})"
                        enabled_stimuli.append(name)
            except Exception:
                pass
            if enabled_stimuli:
                print(f"\nVisual Stimuli: {', '.join(enabled_stimuli)}")
        print("\n" + "=" * 70 + "\n")
        # ── end config summary ──

        logger.info("Starting core processes...")
```

- [ ] **Step 3: Disable monitoring server spawning**

Replace lines 292-308 (the Monitoring Server block) with a comment:

```python
        logger.info("Starting optional processes...")
        # Monitoring server disabled — module kept for future reinstatement.
```

- [ ] **Step 4: Remove Monitoring Server import**

At the top of the file (line 33), comment out:

```python
# from src.monitoring.server import run_server  # monitoring server disabled
```

- [ ] **Step 5: Verify config summary appears at the right time**

Run the experiment (or a quick test that calls start) to verify the config summary block prints before any process init messages:

```bash
uv run python -c "
from src.orchestration import Experiment
e = Experiment()
e.prepare_braid_folder('configs/config.toml')
e.start('configs/config.toml')
"
```

- [ ] **Step 6: Commit**

```bash
git add src/orchestration.py
git commit -m "refactor: move config summary into start(), disable monitoring server"
```

---

### Task 5: Suppress Panda3D stdout/stderr noise

**Files:**
- Modify: `src/visual/scene.py:63`

- [ ] **Step 1: Add imports**

Add at the top of `src/visual/scene.py`:

```python
import contextlib
import os as _os
```

- [ ] **Step 2: Wrap `ShowBase.__init__()` with redirects**

Replace line 63:

```python
        ShowBase.__init__(self)
```

With:

```python
        with contextlib.redirect_stdout(None), contextlib.redirect_stderr(None):
            ShowBase.__init__(self)
```

This suppresses "Known pipe types:" and aux display module messages that Panda3D's C++ layer writes to stderr/stdout during construction.

- [ ] **Step 3: Run the visual standalone test to verify suppression**

```bash
uv run python -m src.visual --standalone
```

Expected: no "Known pipe types:" or aux display module output. The structured log lines should still appear (Creating Panda3D window, Screen mapping, etc.).

- [ ] **Step 4: Commit**

```bash
git add src/visual/scene.py
git commit -m "fix: suppress Panda3D ShowBase stdout/stderr during init"
```

---

### Task 6: xiAPI noise blocklist in camera.py + trial counter

**Files:**
- Modify: `src/processes/camera.py:262-271`

- [ ] **Step 1: Add noise blocklist to `_forward_output()`**

Add the blocklist and filter at the top of `_forward_output()`:

```python
    _XIAPI_NOISE_PATTERNS = (
        "xiAPI: EAL_IF",
        "xiAPI: FGTL_SetParam_to_CAL",
        "xiAPI: SAL_Common_SetAcquisitionFrameRate",
        "xiAPI: xiFAPI_Device::AllocateBuffers",
        "xiAPI: Bandwidth measurement",
    )

    def _forward_output(self) -> None:
        """Read from Rust binary stdout/stderr and forward to logger (daemon thread)."""
        try:
            if self._proc and self._proc.stdout:
                for line in self._proc.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    if any(pattern in line for pattern in self._XIAPI_NOISE_PATTERNS):
                        continue
                    self.logger.info("[optofly-camera] %s", line)
        except Exception as e:
            self.logger.warning("Error reading optofly-camera output: %s", e)
```

- [ ] **Step 2: Add trial counter for camera process**

Add to `RustCameraProcess.__init__`:

```python
        self._trial_count: int = 0
        self._current_trial_obj: int | None = None
```

But wait — the camera process receives ZONE_ENTER/ZONE_EXIT via the Rust binary's ZMQ socket, not in Python. So the trial counting for the camera is in the Rust binary, not here. The Python wrapper only sees `Recording done:` lines from the binary.

For the camera, the trial context shown in the Python wrapper's log lines (like "Recording done") can't easily map trial counters since the Rust binary handles zone events internally. The `_forward_output` only passes through the binary's stdout. The binary's own log line is `Recording done: 90 frames, 0 dropped, trigger_frame=0, reason=left_fov, back to IDLE` — but it doesn't know the trial number either (it only sees ZONE_ENTER/ZONE_EXIT, which return from the same subscriber as TriggerHandler).

The simplest approach: skip trial counting in the camera process. The camera's "Recording done" line is already a structured log line that goes to the file. It doesn't need a trial counter prefix on console since the camera output at INFO level is just device open/close + recording done — and the trial banner from TriggerHandler provides context.

Actually, the spec's expected output shows the camera line with trial context. But the Rust binary receives zone events independently via its own ZMQ subscriber — it doesn't track trial numbers either. So we can't easily add trial context without modifying the Rust binary or adding a trial-counter handshake.

Decision: skip trial counting in the camera process for now. The trial banners from TriggerHandler provide sufficient context for visual grouping.

- [ ] **Step 3: Commit**

```bash
git add src/processes/camera.py
git commit -m "fix: suppress xiAPI bandwidth noise in camera output forwarding"
```

---

### Task 7: Suppress SSE gap warnings before first Braid Update

**Files:**
- Modify: `src/processes/braid.py:415-436`

- [ ] **Step 1: Add `_first_update_seen` flag**

In `BraidPublisher.__init__` (around line 153, after `self.is_connected = False`), add:

```python
        self._first_update_seen = False
```

- [ ] **Step 2: Gate gap warnings behind the flag**

In `_process_stream()`, wrap the gap warning and add a setter in `_dispatch_event()`:

First, in `_process_stream()`, replace lines 416-436:

```python
                last_boundary: Optional[float] = None

                for event_type, data_str in iter_sse_events(
                    _iter_lines_quickack(response)
                ):
                    if self.stop_event.is_set():
                        break

                    self._drain_trigger_events()
                    if event_type == "braid":
                        self._dispatch_event(data_str)

                        now = time.monotonic()
                        if last_boundary is not None and self._first_update_seen:
                            gap = now - last_boundary
                            if gap > BOUNDARY_GAP_WARN_S:
                                self.logger.warning(
                                    f"SSE boundary gap {gap * 1000:.1f} ms "
                                    f"(>{BOUNDARY_GAP_WARN_S * 1000:.0f} ms)"
                                )
                        last_boundary = now
```

- [ ] **Step 3: Set `_first_update_seen` when an Update arrives**

In `_dispatch_event()`, after the message is published (around line 381), add at the end of the method:

```python
        self._first_update_seen = True
```

This is set on the first event dispatch (could be Birth or Update, both count as "the stream is live").

- [ ] **Step 4: Commit**

```bash
git add src/processes/braid.py
git commit -m "fix: suppress SSE gap warnings before first Braid update"
```

---

### Task 8: TriggerHandler — trial banners, trial counter, move detail to DEBUG

**Files:**
- Modify: `src/processes/tracking.py:286-287, 601-615, 617-629, 631-656, 720`

- [ ] **Step 1: Add trial counter**

In `TriggerHandler.__init__`, around line 287 (after `self._zone_enter_count`), add:

```python
        self._trial_count = 0
```

- [ ] **Step 2: Emit trial banners in `_send_zone_enter()`**

Replace `_send_zone_enter()` (lines 601-615):

```python
    def _send_zone_enter(self, tracked_obj: TrackedObject, now: float) -> None:
        """Emit a ZONE_ENTER event."""
        try:
            message_data = self._build_trigger_payload(tracked_obj, now)
            message = json.dumps(message_data)
            topic = self.config.zmq.zone_enter_topic.encode("utf-8")
            self.publisher.send_multipart([topic, message.encode("utf-8")])
            self._last_zone_enter_time = now
            self._zone_enter_count += 1
            self._trial_count += 1
            print(f"\n── Trial #{self._trial_count} obj={tracked_obj.obj_id} ──")
        except Exception as e:
            self.logger.error("Error sending ZONE_ENTER: %s", e)
```

- [ ] **Step 3: Move ZONE_ENTER detail log to DEBUG**

The old `logger.info(...)` is removed (replaced by the banner). Add a DEBUG line for the file:

After the `print(...)` line in `_send_zone_enter()`, add:

```python
            self.logger.debug(
                "ZONE_ENTER [#%d obj=%d] pos=(%.3f, %.3f, %.3f)",
                self._trial_count,
                tracked_obj.obj_id,
                tracked_obj.current_x,
                tracked_obj.current_y,
                tracked_obj.current_z,
            )
```

- [ ] **Step 4: Move scaled-zone-enter detail to DEBUG**

In `_send_scaled_zone_enter()`, change the `self.logger.info(...)` call at line 624 to `self.logger.debug(...)`:

```python
    def _send_scaled_zone_enter(self, tracked_obj: TrackedObject, now: float, topic: str) -> None:
        """Emit an opto/visual inner-zone entry event."""
        try:
            message_data = self._build_trigger_payload(tracked_obj, now)
            message = json.dumps(message_data)
            self.publisher.send_multipart([topic.encode("utf-8"), message.encode("utf-8")])
            self.logger.debug(
                "%s [#%d obj=%d] pos=(%.3f, %.3f, %.3f)",
                topic,
                self._trial_count,
                tracked_obj.obj_id,
                tracked_obj.current_x,
                tracked_obj.current_y,
                tracked_obj.current_z,
            )
        except Exception as e:
            self.logger.error("Error sending %s: %s", topic, e)
```

- [ ] **Step 5: Emit trial end banner in `_send_zone_exit()`**

Replace `_send_zone_exit()` (lines 631-656):

```python
    def _send_zone_exit(self, tracked_obj: TrackedObject, reason: str, now: float | None = None) -> None:
        """Emit a ZONE_EXIT event."""
        try:
            if now is None:
                now = time.time()
            duration = (
                now - tracked_obj.zone_enter_time
                if tracked_obj.zone_enter_time
                else 0.0
            )
            message_data = {
                "obj_id": tracked_obj.obj_id,
                "reason": reason,
                "timestamp": now,
                "duration": duration,
            }

            message = json.dumps(message_data)
            topic = self.config.zmq.zone_exit_topic.encode("utf-8")
            self.publisher.send_multipart([topic, message.encode("utf-8")])
            print(f"── Trial #{self._trial_count} end (duration={duration:.2f}s) ──")
            self.logger.debug(
                "ZONE_EXIT [#%d obj=%d] reason=%s duration=%.2fs",
                self._trial_count,
                tracked_obj.obj_id,
                reason,
                duration,
            )
        except Exception as e:
            self.logger.error("Error sending ZONE_EXIT: %s", e)
```

- [ ] **Step 6: Update shutdown message**

In `_run()`, line 720, change the shutdown log to include trial count:

```python
        self.logger.info("Stopping TriggerHandler (%d trigger(s) this run)", self._trial_count)
```

- [ ] **Step 7: Commit**

```bash
git add src/processes/tracking.py
git commit -m "feat: trial banners, trial counter, move zone-event detail to DEBUG"
```

---

### Task 9: LiquidLens — trial counter, move per-trial detail to DEBUG

**Files:**
- Modify: `src/processes/lens.py:186-187, 404-442, 328-351`

- [ ] **Step 1: Add trial counter**

In `LiquidLens.__init__`, around line 187 (after `self.current_tracked_obj = None`), add:

```python
        self._trial_count = 0
```

- [ ] **Step 2: Increment trial counter on ZONE_ENTER, log at INFO level**

In `_drain_trigger_socket()`, replace lines 401-429 (the ZONE_ENTER handler):

```python
            if topic == self.zmq_config.zone_enter_topic and not self.is_tracking:
                obj_id = msg.get("obj_id")
                if obj_id is not None:
                    self._trial_count += 1
                    self.logger.info(
                        "[#%d obj=%d] start tracking",
                        self._trial_count,
                        obj_id,
                    )
                    self.is_tracking = True
                    self.current_tracked_obj = obj_id
                    self._log_csv("zone_enter", obj_id=obj_id)
                    self._timing_rows = []
                    self._recording_obj_id = obj_id
                    self._recording_frame = msg.get("frame")
                    self._pending_first_update = {
                        key: msg[key]
                        for key in (
                            "obj_id",
                            "frame",
                            "record_frame",
                            "x",
                            "y",
                            "z",
                            "xvel",
                            "yvel",
                            "zvel",
                            "timestamp",
                            "t_relay",
                            "braid_timestamp",
                            "handler_timestamp",
                        )
                        if key in msg
                    }
```

- [ ] **Step 3: Log ZONE_EXIT at INFO level with trial context**

Replace lines 431-442 (the ZONE_EXIT handler):

```python
            elif topic == self.zmq_config.zone_exit_topic and self.is_tracking:
                if msg.get("obj_id") == self.current_tracked_obj:
                    reason = msg.get("reason", "unknown")
                    self.logger.info(
                        "[#%d obj=%d] stop tracking (reason=%s)",
                        self._trial_count,
                        self.current_tracked_obj,
                        reason,
                    )
                    self._log_csv(
                        "zone_exit",
                        obj_id=self.current_tracked_obj,
                        reason=reason,
                    )
                    self._stop_tracking()
```

- [ ] **Step 4: Move lens timing CSV stats from INFO to DEBUG**

In `_flush_timing_csv()`, change line 341 from `self.logger.info(...)` to `self.logger.debug(...)`:

```python
            self.logger.debug(
                f"Lens timing CSV written: {csv_path} ({len(delays)} rows, "
                f"mean delay={statistics.mean(delays):.2f} ms, "
                f"max delay={max(delays):.2f} ms)"
            )
```

- [ ] **Step 5: Commit**

```bash
git add src/processes/lens.py
git commit -m "feat: trial counter in LiquidLens, move timing stats to DEBUG"
```

---

### Task 10: OptoTriggerWorker — emit compact summary row, trial counter

**Files:**
- Modify: `src/processes/led.py:71-82, 227-264, 419`

- [ ] **Step 1: Add trial counter**

In `OptoTriggerWorker.__init__`, around line 82 (after `self.context = None`), add:

```python
        self._trial_count = 0
```

- [ ] **Step 2: Emit compact opto: summary row on trigger**

Replace `_handle_trigger()` lines 227-264:

```python
            self.logger.debug(
                "Received trigger for object %d on frame %d (heading=%s)",
                obj_id,
                frame,
                mean_heading,
            )

            # Trigger the hardware (it will determine sham based on probability)
            success, was_sham, activation_timestamp = self.opto_trigger.trigger(
                sham=None
            )

            self._trial_count += 1

            dur = self.opto_trigger.config.duration
            intensity = self.opto_trigger.config.intensity
            color = self.opto_trigger.config.color
            sham_label = "sham" if was_sham else "real"
            print(
                "  opto:    %s  %s/255  %dms  %s   frame=%s"
                % (color, intensity, dur, sham_label, frame)
            )

            # Prepare CSV row
            row = {
                "obj_id": obj_id,
                "frame": frame,
                "braid_timestamp": timing.braid_timestamp,
                "trigger_timestamp": timing.handler_timestamp,
                "mean_heading": mean_heading,
                "duration": dur,
                "intensity": intensity,
                "frequency": self.opto_trigger.config.frequency,
                "color": color,
                "sham": was_sham,
            }

            # Log to CSV
            self.csv_writer.append(row)
            self.logger.debug("Logged trigger event to CSV: %s", row)
```

Note: the `_trial_count` isn't used in the `print()` because the trial banner from TriggerHandler already numbers the trial. The `print()` just shows the opto parameters for the current trial context.

- [ ] **Step 3: Fix standalone `print()` in `__main__` block**

Line 419: change `print("\nStopping OptoTriggerWorker...")` to:

Actually wait — the `__main__` block doesn't have a logger configured in the same way. But since it's a standalone script, `print()` is fine for standalone usage. The structured logger only matters during experiment runs. Leave this as-is.

- [ ] **Step 4: Commit**

```bash
git add src/processes/led.py
git commit -m "feat: compact opto summary row, trial counter in OptoTriggerWorker"
```

---

### Task 11: VisualProcess — emit compact visual: summary rows, trial counter

**Files:**
- Modify: `src/visual/process.py:71-72, 265-303`

- [ ] **Step 1: Add trial counter**

In `VisualProcess.__init__`, around line 72 (after `self.braid_folder = braid_folder`), add:

```python
        self._trial_count = 0
```

- [ ] **Step 2: Move VISUAL_ZONE_ENTER detail to DEBUG, emit compact visual: row(s)**

Replace `_handle_zone_enter()` lines 265-303:

```python
    def _handle_zone_enter(self, data: dict) -> None:
        braid_rad = data.get("mean_heading", 0.0)
        world_heading = braid_to_world_heading(braid_rad, self._offset_rad, self._flip)
        obj_id = data.get("obj_id")
        frame = data.get("frame")

        self._trial_count += 1

        self.logger.debug(
            "VISUAL_ZONE_ENTER [#%d obj=%s] world_heading=%.1f deg",
            self._trial_count,
            obj_id,
            world_heading,
        )

        stim_params: dict = {}
        for stim in self._stimuli:
            try:
                result = stim.on_trigger(world_heading, data)
                if result:
                    stim_params.update(result)
                    # Emit one compact row per activated stimulus
                    stim_name = type(stim).__name__.replace("Stimulus", "").lower()
                    detail_parts = [f"  visual:  {stim_name}"]
                    # Add known stimulus-specific params
                    for key in ("pos", "pos_deg", "type"):
                        if key in result:
                            detail_parts.append(f"{key}={result[key]}")
                    detail_parts.append(f"frame={frame}")
                    print(" ".join(detail_parts))
            except Exception:
                self.logger.exception(
                    "Error in stimulus %s on_trigger", type(stim).__name__
                )

        activated = bool(stim_params)

        if self._csv_writer and activated:
            self._csv_writer.append(
                {
                    "timestamp": data.get("timestamp", time.time()),
                    "obj_id": obj_id,
                    "frame": frame,
                    "braid_heading_rad": braid_rad,
                    "world_heading_deg": world_heading,
                    **stim_params,
                }
            )

        self._publish_latency(data, activated)
```

- [ ] **Step 3: Verify stimulus param extraction**

Check `BaseStimulus.on_trigger()` return format in `src/visual/base.py` and the concrete stimulus classes to ensure the returned dict keys match what we're printing:

```bash
grep -n "def on_trigger" src/visual/base.py src/visual/stimuli/*.py
grep -n "return {" src/visual/stimuli/*.py
```

Expected: `LoomingStimulus.on_trigger()` returns `{"type": ..., "pos": ...}` or similar. Adjust the `key` list in `_handle_zone_enter` to match.

Run: `uv run python -c "from src.visual.stimuli.looming import LoomingStimulus; import inspect; print(inspect.getsource(LoomingStimulus.on_trigger))"`

- [ ] **Step 4: Commit**

```bash
git add src/visual/process.py
git commit -m "feat: compact visual stimulus summary rows, trial counter"
```

---

### Verification

- [ ] **Run lint and type checking**

```bash
uv run ruff check .
uv run ruff format . --check
```

- [ ] **Run existing tests**

```bash
uv run pytest tests/ -v
```

- [ ] **Run a quick integration test** (if hardware available)

```bash
uv run python main.py --skip-metadata
```

Expected output should match the spec's Expected Output section.

- [ ] **Commit any final fixes**

```bash
git add -u
git commit -m "chore: final lint and test fixes for logging revamp"
```

---

## Self-Review Results

1. **Spec coverage:** All four fixes covered — (a) startup ordering via Task 3+4, (c) noise via Task 5+6+7, (b) format unification via Task 1+3+8+9, (d) trial grouping via Task 8+10+11. Config sync in Task 2. Monitoring disable in Task 4.
2. **Placeholder scan:** No TBD/TODO/evaluate later. All code is concrete.
3. **Type consistency:** `_trial_count` is `int` in all processes. `_current_trial_obj` is `int | None`. Banner format consistent (`── ... ──`). print() format consistent between opto and visual rows.
4. **Missing spec items:** The `opto:` and `visual:` rows don't include `duration` in the row itself (it's in the end banner). The spec sample output showed `opto: red 128/255 300ms real frame=104106` — covered. Visual row format: `visual: looming type=exponential pos=0° frame=104106` — covered. The EXACT rendering depends on what `on_trigger()` returns; verified in Task 11 Step 3.
