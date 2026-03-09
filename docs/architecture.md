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
      - Temporal: tracked continuously for >= min_trajectory_time?
      - Cooldown: >= min_trigger_interval since last trigger?
      - Velocity: > min_velocity?
    |
    | ZMQ PUB (topic: TRIGGER, port: 5556)
    |
    +---> CameraProcess       (records 500fps video)
    +---> OptoTriggerWorker   (activates LED)
    +---> VisualStimuliProcess (displays patterns)
    +---> LiquidLens           (adjusts focus)
```

## Process Model

All processes inherit from `WorkerProcess` (`src/utils/worker.py`) and run as `multiprocessing.Process` instances.

| Process | ZMQ Role | Source |
|---------|----------|--------|
| BraidPublisher | PUB on port 5555 (topic: BRAID) | `src/processes/braid.py` |
| TriggerHandler | SUB on 5555, PUB on 5556 (topic: TRIGGER) | `src/processes/tracking.py` |
| CameraProcess | SUB on 5556 | `src/processes/camera.py` |
| OptoTriggerWorker | SUB on 5556 | `src/processes/led.py` |
| VisualStimuliProcess | SUB on 5556 | `src/processes/visual.py` |
| LiquidLens | SUB on 5556 (TRIGGER + LENS) | `src/processes/lens.py` |

## ZMQ Message Formats

**BRAID topic** (BraidPublisher → TriggerHandler):
```json
{"Update": {"obj_id": 1, "x": 0.01, "y": -0.02, "z": 0.18, ...}}
```

**TRIGGER topic** (TriggerHandler → all consumers):
```json
{
  "obj_id": 1,
  "frame": 12345,
  "braid_timestamp": 123456.789,
  "trigger_timestamp": 123456.790,
  "mean_heading": 1.57
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
- All inter-process communication is one-way (pub/sub). Processes do not acknowledge triggers.

## Predictive Lens Tracking (Experimental)

On the `feature/predictive-lens-tracking` branch:
- TriggerHandler predicts if the fly trajectory will intersect the trigger zone
- Sends an early `LENS` topic message to LiquidLens before the actual trigger
- LiquidLens starts tracking the fly's predicted z-position proactively
- Enable with `[liquid_lens.prediction] enabled = true` and `horizon = 1.5` (seconds)
