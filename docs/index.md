# OptoFly Documentation

Real-time tracking and closed-loop optogenetic stimulation system for flying insects.

## Contents

| Document | Description |
|----------|-------------|
| [Setup](setup.md) | Installation, configuration, and running experiments |
| [Architecture](architecture.md) | System architecture, data flow, and ZMQ topology |
| [Calibration](calibration.md) | Visual stimuli and liquid lens calibration procedures |
| [Visual Stimuli](visual-stimuli.md) | Developer guide for the stimulus rendering system |
| [Visual Stimuli — Panda3D](visual-stimuli-panda3d.md) | Developer guide for the Panda3D stimulus pipeline (tutorial) |
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
- **Visual Stimuli** — Configurable patterns (looming, vertical bar, static)
- **Monitoring** — Web dashboard for real-time trigger visualization
