# OptoFly

Real-time tracking and closed-loop optogenetic stimulation system for flying insects.

Integrates Braid 3D fly tracking with triggered video recording, optogenetic LED stimulation, dynamic autofocus, and configurable visual stimuli.

## Documentation

New to OptoFly? Start with [**Getting Started**](docs/getting-started.md). It walks through the entire pipeline in order, from install through every calibration step to your first experiment.

Full documentation is in the [`docs/`](docs/) folder:

- [Getting Started](docs/getting-started.md) — installation, the full calibration pipeline in order, running experiments
- [Calibration](docs/calibration.md) — camera intrinsics, Braid, liquid lens, camera FOV, and arena heading calibration, in depth
- [Architecture](docs/architecture.md) — system overview, data flow, ZMQ topology
- [Visual Stimuli — Panda3D](docs/visual-stimuli-panda3d.md) — developer guide for the Panda3D stimulus pipeline
- [Camera](docs/camera.md) — Ximea high-speed camera system
- [Opto Trigger](docs/opto-trigger.md) — Arduino LED firmware and protocol
- [Troubleshooting](docs/troubleshooting.md) — common issues and fixes

## Software Quick Start

This gets the software installed and runnable. It skips camera, lens, and arena calibration, which a new rig needs first; see [Getting Started](docs/getting-started.md) for the full sequence.

```bash
# Install dependencies
uv sync

# Copy and customize configs
cp configs/config.example.toml configs/config.toml
cp configs/visual_stimuli.example.toml configs/visual_stimuli.toml

# Run experiment (start Braid recording first)
uv run python main.py
```

## Real-Time Tracking And Liquid Lens Path

OptoFly receives live Braid tracking data from the Braid model server SSE endpoint
(`http://<host>:8397/events`) through `BraidPublisher`.

The relay now has two ZMQ outputs:

- `BRAID` on `braid_port` (`5555` by default): the full tracking stream for
  `TriggerHandler`, monitoring, and tools that need every object update.
- `ACTIVE_BRAID` on `active_braid_port` (`5557` by default): a lens-specific
  fast lane containing only updates for the object currently inside the trigger
  zone.

`TriggerHandler` still consumes the full `BRAID` stream and emits `ZONE_ENTER`
and `ZONE_EXIT` on `trigger_port` (`5556`). `BraidPublisher` also listens to
those zone events so it knows which object is active. When a Braid `Update`
matches that active object, it republishes the inner update directly on
`ACTIVE_BRAID`.

`LiquidLens` subscribes to `ZONE_ENTER` / `ZONE_EXIT` for lifecycle control and
to `ACTIVE_BRAID` for focus updates. Its active-update socket is configured as
latest-only (`lens_update_conflate = true`), so stale focus updates are dropped
intentionally. This keeps the lens from spending serial-write time on old
positions when updates arrive faster than the hardware can apply them.

The first lens focus command after `ZONE_ENTER` uses the position and velocity
included in the `ZONE_ENTER` payload. That avoids waiting for the next matching
Braid update before the lens starts moving.

Relevant config keys live under `[zmq]`:

```toml
braid_port = 5555
trigger_port = 5556
active_braid_port = 5557

braid_topic = "BRAID"
zone_enter_topic = "ZONE_ENTER"
zone_exit_topic = "ZONE_EXIT"
opto_enter_topic = "OPTO_ZONE_ENTER"
visual_enter_topic = "VISUAL_ZONE_ENTER"
active_braid_topic = "ACTIVE_BRAID"

braid_pub_hwm = 1000
lens_update_conflate = true
```
