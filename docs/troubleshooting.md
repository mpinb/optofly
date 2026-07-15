# Troubleshooting

## Setup

**Config files missing:**
```bash
cp configs/config.example.toml configs/config.toml
cp configs/visual_stimuli.example.toml configs/visual_stimuli.toml
```

**Camera permission denied:**
```bash
sudo usermod -a -G video $USER
# Log out and back in, then verify:
groups
```

**ZMQ connection refused:**
```bash
netstat -tulpn | grep 5555
# If nothing found, verify Braid is running and BraidPublisher is started
```

## Runtime

**No triggers generated:**

Check trigger handler logs for:
- Are flies being tracked? (look for "Update" messages)
- Are flies heading toward center? (check heading values vs. `heading_cone_deg`)
- Are flies in the trigger zone? (check position vs. camera FOV x/y and `z_min`/`z_max`)
- Is cooldown expired? (check time since last trigger)

Enable debug logging:
```toml
[logging]
level = "DEBUG"
```

**Visual stimuli not appearing:**
- Verify `enabled = true` in the relevant subsection of `configs/visual_stimuli.toml`
- Test standalone: `uv run python -m src.visual --standalone` (opens small 1280×320 window)
- Check window appears at `window_x_offset` on the correct monitor
- Verify `"Registered: <StimulusName>"` appears in startup logs
- For Panda3D: ensure `unstash()` is called in `on_trigger()` — stashed nodes are invisible

**Experiment aborts at startup with an `OptoTriggerWorker` error:**

With `[opto_trigger] active = true` the worker is a critical process, so an unopenable Arduino port aborts the whole startup. Check the symlink and permissions:

```bash
ls -l /dev/opto_trigger       # udev symlink present? (see configs/config.example.toml)
groups | grep -q dialout && echo "in dialout group"
```

With `active = false` a missing Arduino is *not* fatal: the worker logs a warning and the experiment continues without the arena backlight.

**Camera not recording:**

Run the preflight check first — it reports all four prerequisites and what to do about each:

```bash
uv run python -c "from src.processes.camera import check_camera_prerequisites as c; \
[print(k, v) for k, v in c('configs/config.toml').items()]"
```

Typical output:

```
camera_binary ✓ optofly-camera found at .../target/release/optofly-camera
ffmpeg ✓ ffmpeg found at /usr/bin/ffmpeg
save_folder_writable ✓ save folder /mnt/data/videos exists and is writable
trigger_port ✗ nothing is bound to trigger port 5556 — the experiment is not running.
```

A failing `trigger_port` is expected unless `main.py` is live; the other three should all be `✓`.
The check creates nothing and touches no hardware, so it is safe to run at any time.

If all four pass and the camera still doesn't record, the cause is usually below this layer:

```bash
lsusb | grep -i ximea        # camera detected at all?
groups | grep -q video && echo "in video group"
df -h /mnt/data              # disk space
```

**Video encoding slow:**
```bash
sudo ubuntu-drivers autoinstall
# Reboot, then verify:
ffmpeg -encoders | grep nvenc
```

**Liquid lens not responding:**
```bash
ls -l /dev/optotune_icc1c     # udev symlink present? (see configs/config.example.toml)
ls -l /dev/ttyUSB*            # ...and what it should point at
groups | grep -q dialout && echo "in dialout group"
ls calibrations/liquid_lens.csv
```

If the symlink is missing, create the udev rule documented in the `[liquid_lens]`
section of `configs/config.example.toml` rather than pointing `port` at a bare
`/dev/ttyUSBn` — that number changes between reboots and between devices.

If the device is there but you get permission denied, add yourself to `dialout`
(`sudo usermod -a -G dialout $USER`, then log out and back in) rather than
`chmod 666` — the latter is undone on every replug.

The lens also fails to start on a bad calibration file. `calibrations/liquid_lens.csv`
must have exactly two columns named `z` and `dpt`. The
[`liquid-lens-calibration`](https://github.com/elhananby/liquid-lens-calibration)
repo emits a `diopter` column instead — rename it to `dpt` before use.

**ICC-1C-specific setup notes** (controller as of the `icc-1c` branch):
- Power the controller from the dedicated barrel supply connector, or a USB-C
  port rated for >3A power delivery, not a random USB port. Current spikes
  during lens actuation can exceed what a typical USB port supplies.
- The FPC flex cable (Extension Board) is **not** hot-pluggable — always
  power off the controller before connecting/disconnecting it, or risk
  EEPROM corruption/damage. The Hirose connector is safe to hot-plug.
- If focal power mode won't engage, confirm the connected lens has EEPROM
  calibration data — lenses without it only support current mode
  (`ICC1C.to_focal_power_mode()` raises `LensCommandError` in that case).
- Smart Step (faster lens settling time) is **not** controlled by this
  driver — it must be configured once via Optotune Cockpit and saved as the
  startup snapshot on the controller itself.

## Performance

**Visual stimuli FPS drops below target (Panda3D pipeline):**

Causes:
- `update(dt)` allocating objects or doing heavy math each frame
- Per-frame `removeNode()`/`attachNewNode()` cycles — prefer `stash()`/`unstash()`
- Too many scene-graph nodes

Fixes: Pre-calculate constants in `setup()` or `__init__()`. Keep `update()` to simple arithmetic.
Use `stash()`/`unstash()` for repeated show/hide — `removeNode` + `attachNewNode` is significantly slower.

**Camera frame drops:**
- Check CPU usage during recording
- Verify NVENC is active (check ffmpeg output in camera logs)
- Check disk I/O bandwidth

## Calibration

**Calibration window doesn't appear:**
- Check displays are configured and connected
- Verify `window_x_offset` in config matches your display setup

**Stimuli appear at wrong positions after calibration:**
- Re-run `uv run python -m src.tools.calibrate_heading` with the tracked object more carefully positioned
- Verify the object was pointing directly at each calibration screen (not just near it)
- Check `braid_heading_offset_rad` and `braid_heading_flip` in `[visual_stimuli.arena]`

**Can't see tracking data in Braid:**
- Confirm cameras are running and calibrated in Braid
- Verify the tracked object is within the tracking volume
