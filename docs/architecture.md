# Architecture

## Data Flow

```
Braid Server (100fps 3D tracking)
    |
    | HTTP SSE  →  BraidPublisher  →  ZMQ PUB (topic: BRAID, port: 5555)
    |
    v
TriggerHandler (src/processes/tracking.py)
    Evaluates:
      - Spatial: fly within rectangular FOV (x/y) + z bounds?
      - Heading: moving toward arena center (within heading_cone_deg)?
      - Velocity: > min_velocity?
    On enter: emits ZONE_ENTER
    On leave/death/timeout: emits ZONE_EXIT
    |
    | ZMQ PUB (topics: ZONE_ENTER / ZONE_EXIT, port: 5556)
    |
    +---> CameraProcess       (records while fly is in zone)
    +---> LiquidLens           (tracks focus while fly is in zone)
    +---> OptoTriggerWorker   (one-shot LED on ZONE_ENTER)
    +---> VisualStimuliProcess (one-shot stimulus on ZONE_ENTER)
```

## Process Model

All processes inherit from `WorkerProcess` (`src/utils/worker.py`) and run as `multiprocessing.Process` instances.

| Process | ZMQ Role | Source |
|---------|----------|--------|
| BraidPublisher | PUB on port 5555 (topic: BRAID) | `src/processes/braid.py` |
| TriggerHandler | SUB on 5555, PUB on 5556 (topics: ZONE_ENTER, ZONE_EXIT) | `src/processes/tracking.py` |
| CameraProcess | SUB on 5556 (ZONE_ENTER, ZONE_EXIT) | `src/processes/camera.py` |
| OptoTriggerWorker | SUB on 5556 (ZONE_ENTER) | `src/processes/led.py` |
| VisualStimuliProcess | SUB on 5556 (ZONE_ENTER) | `src/processes/visual.py` |
| LiquidLens | SUB on 5555 (BRAID) + 5556 (ZONE_ENTER, ZONE_EXIT) | `src/processes/lens.py` |

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

## Important Notes

- The ZMQ BRAID feed is only live when the full stack is running. Standalone tools that need tracking data must connect directly to the Braid HTTP SSE endpoint (`http://<braid_url>/events`).
- BraidPublisher reads from `http://<url>/events` as a streaming SSE connection and re-publishes via ZMQ.
- All inter-process communication is one-way (pub/sub). Processes do not acknowledge zone events.
- Camera and LiquidLens use both ZONE_ENTER and ZONE_EXIT (continuous tracking). OptoTrigger and VisualStimuli are one-shot (ZONE_ENTER only).
