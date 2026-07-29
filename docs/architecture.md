# Architecture

## Data Flow

```
Braid Server (100fps 3D tracking)
    |
    | HTTP SSE  →  BraidPublisher  →  ZMQ PUB (topic: BRAID, port: 5555)
    |                     |
    |                     +→  ZMQ PUB (topic: ACTIVE_BRAID, port: 5557)
    |                         Updates for the one object currently in the zone
    v
TriggerHandler (src/processes/tracking.py)
    Monitors tracked objects and applies entry gates:
      1. Object must exist for ≥ min_tracking_age (filters noise)
      2. Must satisfy global cooldown_period since last ZONE_ENTER
      3. Must be within trigger zone (camera FOV x/y + z bounds)
      4. Velocity must be in [min_velocity, max_velocity] range
      5. Must be heading toward arena center (within heading_cone_deg)
    |
    | ZMQ PUB (topics: ZONE_ENTER / ZONE_EXIT / OPTO_ZONE_ENTER / VISUAL_ZONE_ENTER, port: 5556)
    |
    +---> RustCameraProcess    (starts recording on ZONE_ENTER; stops on ZONE_EXIT)
    +---> LiquidLens           (starts on ZONE_ENTER; then follows ACTIVE_BRAID until ZONE_EXIT)
    +---> OptoTriggerWorker    (one-shot LED on OPTO_ZONE_ENTER only)
    +---> VisualProcess        (Panda3D; renders stimuli on VISUAL_ZONE_ENTER, one-shot)
    +---> Monitoring Server    (web dashboard, ZONE_ENTER only, optional)
    +---> BraidPublisher       (feedback: learns which object is active)

OptoTriggerWorker, VisualProcess, LiquidLens
    |
    | ZMQ PUSH (one LATENCY message per trigger, port: 5558)
    v
LatencyLogger  →  latency.csv in the braid folder
```

TriggerHandler is the single admission controller. Each object can produce at most one `ZONE_ENTER`/`ZONE_EXIT` pair per visit to the trigger volume, with re-entry treated as a new cycle subject only to the global cooldown period. `OPTO_ZONE_ENTER`/`VISUAL_ZONE_ENTER` are separate one-shot events fired once the object, already inside the outer `ZONE_ENTER` zone, reaches a smaller nested zone sized by `opto_zone_scale`/`visual_zone_scale`; at `scale=1.0` they fire on the same frame as `ZONE_ENTER`.

`BraidPublisher` both publishes and subscribes: it forwards the full tracking stream on `BRAID`, and it also subscribes to `ZONE_ENTER`/`ZONE_EXIT` so it knows which object is currently active. Updates for that object are republished on the `ACTIVE_BRAID` fast lane (`SNDHWM = 1`, and consumers set `CONFLATE`), so the lens always focuses on the newest position rather than working through a backlog.

## Process Model

All processes inherit from `WorkerProcess` (`src/utils/worker.py`) and run as `multiprocessing.Process` instances.

| Process | ZMQ Role | Source |
|---------|----------|--------|
| BraidPublisher | PUB on 5555 (BRAID) + PUB on 5557 (ACTIVE_BRAID) + SUB on 5556 (ZONE_ENTER, ZONE_EXIT) | `src/processes/braid.py` |
| TriggerHandler | SUB on 5555 (BRAID), PUB on 5556 (ZONE_ENTER, ZONE_EXIT, OPTO_ZONE_ENTER, VISUAL_ZONE_ENTER) | `src/processes/tracking.py` |
| RustCameraProcess | SUB on 5556 (ZONE_ENTER, ZONE_EXIT, kill) | `src/processes/camera.py` |
| OptoTriggerWorker | SUB on 5556 (OPTO_ZONE_ENTER) + PUSH on 5558 (LATENCY) | `src/processes/led.py` |
| VisualProcess | SUB on 5556 (VISUAL_ZONE_ENTER) + PUSH on 5558 (LATENCY) | `src/visual/process.py` |
| LiquidLens | SUB on 5557 (ACTIVE_BRAID) + SUB on 5556 (ZONE_ENTER, ZONE_EXIT) + PUSH on 5558 (LATENCY) | `src/processes/lens.py` |
| LatencyLogger | PULL bind on 5558 (LATENCY) | `src/processes/latency_logger.py` |
| Monitoring Server | SUB on 5556 (ZONE_ENTER) | `src/monitoring/server.py` |

Port numbers above are the defaults from `[zmq]` in `configs/config.toml`; all four are configurable (`braid_port`, `trigger_port`, `active_braid_port`, `latency_port`), as are the topic names.

`LatencyLogger` is core and always-on — started immediately after `TriggerHandler`, before any optional process. It is the sole writer of `latency.csv`; a dedicated process avoids the header race three concurrent appenders would hit on the first trigger. A dead `LatencyLogger` only loses latency data, it never aborts the experiment.

## ZMQ Message Formats

**BRAID topic** (BraidPublisher → TriggerHandler):
```json
{"Update": {"obj_id": 1, "x": 0.01, "y": -0.02, "z": 0.18, "...": "..."}}
```

**ZONE_ENTER topic** (TriggerHandler → consumers):
```json
{
  "obj_id": 1,
  "frame": 12345,
  "timestamp": 123456.790,
  "braid_timestamp": 123456.780,
  "handler_timestamp": 123456.790,
  "x": 0.01,
  "y": -0.02,
  "z": 0.18,
  "xvel": 0.05,
  "yvel": -0.12,
  "zvel": 0.01,
  "mean_heading": 1.57
}
```

`xvel`/`yvel`/`zvel` are the object's most recent instantaneous velocity (not the mean over `HEADING_HISTORY_SIZE` used for `mean_heading`). `LiquidLens` uses them to seed its first focus command immediately on `ZONE_ENTER`, without waiting for the next `ACTIVE_BRAID` update.

**The two timestamps are on different clocks and must not be conflated:**

- `timestamp` and `handler_timestamp` are both TriggerHandler's own local receipt-time clock. All velocity, age, and cooldown arithmetic uses these.
- `braid_timestamp` is the Triggerbox-clock-model value that Braid itself computed, lifted from the SSE envelope's `trigger_timestamp`. It exists solely so `LatencyLogger` can measure end-to-end latency. It is `null` when Braid supplied none for that sample — note Braid serializes an unset value as the JSON token `NaN`, which `json.loads` parses to `float('nan')`, not `None`, so `BraidPublisher` normalizes it explicitly.

**LATENCY** (OptoTriggerWorker / VisualProcess / LiquidLens → LatencyLogger, PUSH/PULL):
```json
{
  "system": "opto",
  "obj_id": 1,
  "frame": 12345,
  "record_frame": 12280,
  "braid_timestamp": 123456.780,
  "trigger_timestamp": 123456.790,
  "activation_timestamp": 123456.812,
  "sham": false
}
```

`system` is `"opto"`, `"visual"`, or `"lens"`. `LatencyLogger` computes `latency_ms = (activation_timestamp - braid_timestamp) * 1000` for non-sham trials. `LiquidLens` publishes only for the first commanded diopter per trial, not every subsequent tracking update.

`frame` is the Braid frame at which *this* system fired (i.e. at `OPTO_ZONE_ENTER` / `VISUAL_ZONE_ENTER`), while `record_frame` is the frame at which the outer `ZONE_ENTER` fired and camera recording began. The two differ whenever `opto_zone_scale`/`visual_zone_scale` is below `1.0`; `record_frame` is what aligns a stimulus onset against the recorded video.

**ZONE_EXIT topic** (TriggerHandler → camera, lens, monitoring):
```json
{
  "obj_id": 1,
  "reason": "left_fov",
  "timestamp": 123456.980,
  "duration": 0.19
}
```

**Camera kill signal:** the Rust binary subscribes to a bare `kill` topic on `trigger_port`, but nothing in this codebase publishes to it. Shutdown is by SIGTERM from the Python wrapper (`RustCameraProcess._run`). The subscription is vestigial — don't build on it without adding a publisher.

## Configuration Loading

Configurations are loaded via `src/utils/config.py`. Each process has a dedicated config dataclass (for example `BraidPublisherConfig` and `TriggerHandlerConfig`) that reads from TOML and exposes typed attributes with defaults.

`AppConfig` is the root: one TOML parse, the whole dependency tree assembled in the correct order, and no config class ever constructs another. Every per-section constructor delegates to `AppConfig.load()`, so pass `config_path` down to each process rather than reading TOML directly anywhere else.

### TriggerHandler Configuration

Key parameters in `[trigger_handler]`:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `z_min`, `z_max` | 0.0, 0.5 m | Z bounds for the trigger zone (`config.example.toml` narrows these to 0.10/0.25) |
| `heading_cone_deg` | 45° | Angular tolerance for heading-toward-center check |
| `min_velocity` | 0.01 m/s | Minimum speed to consider object moving |
| `max_velocity` | 2.0 m/s | Maximum speed, used to reject tracking noise |
| `min_tracking_age` | 0.1 s | Object age before it can trigger |
| `zone_timeout` | 2.0 s | Auto-emit `ZONE_EXIT` if tracking is lost; also used by camera and liquid lens |
| `cooldown_period` | 10.0 s | Global cooldown between `ZONE_ENTER` events |
| `opto_zone_scale` | 0.5 | Inner zone for `OPTO_ZONE_ENTER`, as a fraction of the outer FOV (centered). Must be in (0.0, 1.0]; `1.0` fires on the same frame as `ZONE_ENTER` |
| `visual_zone_scale` | 1.0 | Same, for `VISUAL_ZONE_ENTER` |

The trigger zone's x/y bounds come from the camera FOV. `zone_timeout` is the single source of truth for follower processes.

## Trigger Lifecycle

When an object crosses into the spatial trigger zone, `TriggerHandler` applies the five entry gates above. If they pass, it emits `ZONE_ENTER` once and marks the object active. While active, heading and velocity are not re-checked; only spatial membership matters. `ZONE_EXIT` is emitted when the object:

1. Leaves the trigger zone.
2. Dies in BRAID.
3. Times out due to missing updates.

After exit, the same object can trigger again if it re-enters the zone after the global cooldown period has elapsed.

## Important Notes

- The ZMQ BRAID feed is only live when the full stack is running. Standalone tools that need tracking data must connect directly to the Braid HTTP SSE endpoint (`http://<braid_url>/events`).
- BraidPublisher reads from `http://<url>/events` as a streaming SSE connection and republishes via ZMQ.
- Zone events are one-way pub/sub: processes never acknowledge them. The one exception to PUB/SUB in the whole codebase is the `LATENCY` channel, which is PUSH/PULL — a many-producer, one-consumer fan-in rather than a broadcast, so each message is delivered to exactly one reader.
- Camera and liquid lens are lifecycle followers only: start on `ZONE_ENTER`, stop on `ZONE_EXIT`, keep no pre-zone state.
- `LiquidLens` has no `active` flag of its own. It is started iff `[camera] active = true`, since autofocus is only meaningful while the high-speed camera is recording.
- Every `*Config` path constructor routes through `AppConfig.load()`, which builds and validates **all nine** config sections regardless of any section's `active` flag. `configs/config.toml` must therefore always contain valid `[liquid_lens]`, `[opto_trigger]`, etc. sections (each with its required `port` key) even when those subsystems are disabled.
