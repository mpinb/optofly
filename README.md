# OptoFly

Real-time tracking and closed-loop optogenetic stimulation system for flying insects.

Integrates Braid 3D fly tracking with triggered video recording, optogenetic LED stimulation, dynamic autofocus, and configurable visual stimuli.

## Documentation

Full documentation is in the [`docs/`](docs/) folder:

- [Setup](docs/setup.md) — installation, configuration, running experiments
- [Architecture](docs/architecture.md) — system overview, data flow, ZMQ topology
- [Calibration](docs/calibration.md) — visual stimuli, liquid lens, and BRAID-to-camera calibration
- [Visual Stimuli](docs/visual-stimuli.md) — developer guide for custom stimuli
- [Camera](docs/camera.md) — Ximea high-speed camera system
- [Opto Trigger](docs/opto-trigger.md) — Arduino LED firmware and protocol
- [Troubleshooting](docs/troubleshooting.md) — common issues and fixes

## Quick Start

```bash
# Install dependencies
uv sync

# Copy and customize configs
cp configs/config.example.toml configs/config.toml
cp configs/visual_stimuli.example.toml configs/visual_stimuli.toml

# Run experiment (start Braid recording first)
uv run python main.py
```

See [docs/setup.md](docs/setup.md) for full setup instructions.
