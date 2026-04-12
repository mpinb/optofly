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
      3. Must be within trigger zone (FOV x/y + z bounds)
      4. Velocity must be in [min_velocity, max_velocity] range
      5. Must be heading toward arena center (within heading_cone_deg)
    |
    | ZMQ PUB (topics: ZONE_ENTER / ZONE_EXIT, port: 5556)
    |
    +---> CameraProcess        (records while fly is in zone)
    +---> LiquidLens           (tracks focus while fly is in zone)
    +---> OptoTriggerWorker    (one-shot LED on ZONE_ENTER)
    +---> VisualStimuliProcess (one-shot stimulus on ZONE_ENTER)
    +---> Monitoring Server    (web dashboard, optional)
```

## Process Model

All processes inherit from `WorkerProcess` (`src/utils/worker.py`) and run as `multiprocessing.Process` instances.

| Process | ZMQ Role | Source |
|---------|----------|--------|
| BraidPublisher | PUB on port 5555 (topic: BRAID) | `src/processes/braid.py` |
| TriggerHandler | SUB on 5555, PUB on 5556 (topics: ZONE_ENTER, ZONE_EXIT) | `src/processes/tracking.py` |
| CameraProcess | SUB on 5556 (ZONE_ENTER, ZONE_EXIT, kill) | `src/processes/camera.py` |
| OptoTriggerWorker | SUB on 5556 (ZONE_ENTER) | `src/processes/led.py` |
| VisualStimuliProcess | SUB on 5556 (ZONE_ENTER) | `src/processes/visual.py` |
| LiquidLens | SUB on 5555 (BRAID) + 5556 (ZONE_ENTER, ZONE_EXIT) | `src/processes/lens.py` |
| Monitoring Server | SUB on 5556 (ZONE_ENTER, ZONE_EXIT) | `src/monitoring/server.py` |

## ZMQ Message Formats

**BRAID topic** (BraidPublisher → TriggerHandler):
```json
{"Update": {"obj_id": 1, "x": 0.01, "y": -0.02, "z": 0.18, ...}}
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

**ZONE_EXIT topic** (TriggerHandler → camera, lens):
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

Configurations are loaded via `src/utils/config.py`. Each process has a dedicated config dataclass (e.g., `BraidPublisherConfig`, `TriggerHandlerConfig`). The config reads from the TOML file and provides typed attributes with defaults.

### TriggerHandler Configuration

Key parameters in `[trigger_handler]` section:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `z_min`, `z_max` | 0.15, 0.25 m | Z bounds for trigger zone |
| `heading_cone_deg` | 45° | Angular tolerance for heading-toward-center check |
| `min_velocity` | 0.01 m/s | Minimum speed to consider object "moving" |
| `max_velocity` | 2.0 m/s | Maximum speed (filters tracking noise) |
| `min_tracking_age` | 0.1 s | Object age before it can trigger (noise filter) |
| `zone_timeout` | 2.0 s | Global timeout: auto-ZONE_EXIT if tracking lost; also used by camera (buffer sizing) and liquid lens (focus tracking) |
| `refractory_period` | 10.0 s | Global cooldown between ZONE_ENTER events |

The trigger zone's x/y bounds are sourced from the camera FOV (not configurable separately). The `zone_timeout` value is the single source of truth — CameraProcess and LiquidLens read it from `[trigger_handler]` rather than maintaining separate timeouts.

## Trigger Entry Gates

When an object enters the spatial trigger zone, `TriggerHandler` applies five sequential gates:

1. **Tracking Age**: Object must have been tracked for ≥ `min_tracking_age` (typically 0.1s). Filters transient noise detections from tracking artifacts.

2. **Refractory Period**: At least `refractory_period` seconds must have elapsed since the last ZONE_ENTER was emitted (global, not per-object). Prevents rapid-fire triggers from a single bouncing trajectory.

3. **Spatial Zone**: Object's (x, y) must be within camera FOV and z must be within [z_min, z_max]. The FOV is the single source of truth for the trigger zone.

4. **Velocity**: Object's speed (xy magnitude) must be in [min_velocity, max_velocity]. Filters stationary objects and unrealistically fast noise.

5. **Heading**: Object must be heading toward the arena center within a cone of `heading_cone_deg` degrees. Filters flies moving away or tangentially.

All five gates must pass in sequence to emit ZONE_ENTER. If any gate fails, the trigger is suppressed with a debug log.

## Important Notes

- The ZMQ BRAID feed is only live when the full stack is running. Standalone tools that need tracking data must connect directly to the Braid HTTP SSE endpoint (`http://<braid_url>/events`).
- BraidPublisher reads from `http://<url>/events` as a streaming SSE connection and re-publishes via ZMQ.
- All inter-process communication is one-way (pub/sub). Processes do not acknowledge zone events.
- Camera and LiquidLens use both ZONE_ENTER and ZONE_EXIT (continuous tracking). OptoTrigger and VisualStimuli are one-shot (ZONE_ENTER only).
- ZONE_EXIT is emitted immediately when an object leaves the spatial zone, or after `zone_timeout` (default 2s) if tracking is lost.
