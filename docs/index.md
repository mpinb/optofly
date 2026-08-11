# OptoFly Documentation

Real-time tracking and closed-loop optogenetic stimulation system for flying insects.

## New to OptoFly? Start here.

[**Getting Started**](getting-started.md) walks through the whole pipeline in order, from a fresh checkout to a running experiment: install, calibrate every camera and the liquid lens, align the arena, bench-test the LED, then run. Each step links to the reference doc below for full detail.

## Reference

| Document | Description |
|----------|-------------|
| [Getting Started](getting-started.md) | Installation, the full calibration pipeline in order, and running experiments |
| [Calibration](calibration.md) | Every calibration procedure in depth: camera intrinsics, Braid, liquid lens, camera FOV, arena heading |
| [Architecture](architecture.md) | System architecture, data flow, and ZMQ topology |
| [Visual Stimuli — Panda3D](visual-stimuli-panda3d.md) | Developer guide for the Panda3D stimulus pipeline (current, used by main.py) |
| [Camera](camera.md) | Ximea high-speed camera system |
| [Opto Trigger](opto-trigger.md) | Arduino LED firmware and serial protocol |
| [Troubleshooting](troubleshooting.md) | Common issues and fixes |

## System Overview

OptoFly integrates multiple hardware and software components for automated closed-loop behavioral experiments:

- **Braid Tracking** — Real-time 3D fly tracking at 100fps
- **Trigger Handler** — Spatial/temporal gating with heading detection
- **Ximea Camera** — High-speed (500fps) triggered video recording
- **Optogenetic Trigger** — LED stimulation control via Arduino
- **Liquid Lens** — Dynamic autofocus with optional predictive tracking
- **Visual Stimuli** — Configurable Panda3D patterns (background, looming, oscillating square)
- **Latency Logger** — Per-trial end-to-end latency records (`latency.csv`) for opto, visual, and lens triggers — see [Camera](camera.md) for `src/tools/frame_alignment.py`, which maps a `latency.csv` trigger to its recorded-video frame
