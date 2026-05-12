# Architecture

## Data Flow

```
Braid Server (100fps 3D tracking)
    |
    | HTTP SSE  →  BraidPublisher  →  ZMQ PUB (topic: BRAID, port: 5555)
    |
    v
TriggerHandler (src/processes/tracking.py)
    Monitors tracked objects and applies entry gates:
      1. Object must exist for ≥ min_tracking_age (filters noise)
      2. Must satisfy global refractory_period since last ZONE_ENTER
      3. Must be within trigger zone (camera FOV x/y + z bounds)
      4. Velocity must be in [min_velocity, max_velocity] range
      5. Must be heading toward arena center (within heading_cone_deg)
    |
    | ZMQ PUB (topics: ZONE_ENTER / ZONE_EXIT, port: 5556)
    |
    +---> RustCameraProcess    (starts recording on ZONE_ENTER; stops on ZONE_EXIT)
    +---> LiquidLens           (starts tracking on ZONE_ENTER; follows BRAID until ZONE_EXIT)
    +---> OptoTriggerWorker    (one-shot LED on ZONE_ENTER only)
    +---> VisualProcess        (Panda3D; renders stimuli on ZONE_ENTER, one-shot)
    +---> Monitoring Server    (web dashboard, optional)
```

TriggerHandler is the single admission controller. Each object can produce at most one `ZONE_ENTER`/`ZONE_EXIT` pair per visit to the trigger volume, with re-entry treated as a new cycle subject only to the global refractory period.

## Process Model

All processes inherit from `WorkerProcess` (`src/utils/worker.py`) and run as `multiprocessing.Process` instances.

| Process | ZMQ Role | Source |
|---------|----------|--------|
| BraidPublisher | PUB on port 5555 (topic: BRAID) | `src/processes/braid.py` |
| TriggerHandler | SUB on 5555, PUB on 5556 (topics: ZONE_ENTER, ZONE_EXIT) | `src/processes/tracking.py` |
| RustCameraProcess | SUB on 5556 (ZONE_ENTER, ZONE_EXIT, kill) | `src/processes/camera.py` |
| OptoTriggerWorker | SUB on 5556 (ZONE_ENTER only) | `src/processes/led.py` |
| VisualProcess | SUB on 5556 (ZONE_ENTER only) | `src/visual/process.py` |
| LiquidLens | SUB on 5555 (BRAID) + 5556 (ZONE_ENTER, ZONE_EXIT) | `src/processes/lens.py` |
| Monitoring Server | SUB on 5556 (ZONE_ENTER, ZONE_EXIT) | `src/monitoring/server.py` |

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
  "x": 0.01,
  "y": -0.02,
  "z": 0.18,
  "mean_heading": 1.57
}
```

**ZONE_EXIT topic** (TriggerHandler → camera, lens, monitoring):
```json
{
  "obj_id": 1,
  "reason": "left_fov",
  "timestamp": 123456.980,
  "duration": 0.19
}
```

**Camera kill signal:**
```
kill
```

## Configuration Loading

Configurations are loaded via `src/utils/config.py`. Each process has a dedicated config dataclass (for example `BraidPublisherConfig` and `TriggerHandlerConfig`) that reads from TOML and exposes typed attributes with defaults.

### TriggerHandler Configuration

Key parameters in `[trigger_handler]`:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `z_min`, `z_max` | 0.15, 0.25 m | Z bounds for the trigger zone |
| `heading_cone_deg` | 45° | Angular tolerance for heading-toward-center check |
| `min_velocity` | 0.01 m/s | Minimum speed to consider object moving |
| `max_velocity` | 2.0 m/s | Maximum speed, used to reject tracking noise |
| `min_tracking_age` | 0.1 s | Object age before it can trigger |
| `zone_timeout` | 2.0 s | Auto-emit `ZONE_EXIT` if tracking is lost; also used by camera and liquid lens |
| `refractory_period` | 10.0 s | Global cooldown between `ZONE_ENTER` events |

The trigger zone's x/y bounds come from the camera FOV. `zone_timeout` is the single source of truth for follower processes.

## Trigger Lifecycle

When an object crosses into the spatial trigger zone, `TriggerHandler` applies the five entry gates above. If they pass, it emits `ZONE_ENTER` once and marks the object active. While active, heading and velocity are not re-checked; only spatial membership matters. `ZONE_EXIT` is emitted when the object:

1. Leaves the trigger zone.
2. Dies in BRAID.
3. Times out due to missing updates.

After exit, the same object can trigger again if it re-enters the zone after the global refractory period has elapsed.

## Important Notes

- The ZMQ BRAID feed is only live when the full stack is running. Standalone tools that need tracking data must connect directly to the Braid HTTP SSE endpoint (`http://<braid_url>/events`).
- BraidPublisher reads from `http://<url>/events` as a streaming SSE connection and republishes via ZMQ.
- All inter-process communication is one-way pub/sub. Processes do not acknowledge zone events.
- Camera and liquid lens are lifecycle followers only: start on `ZONE_ENTER`, stop on `ZONE_EXIT`, keep no pre-zone state.
