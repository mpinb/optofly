# Troubleshooting

## Setup

**Camera binary not found:**
```bash
cd rust/ximea_camera
cargo build --release
ls target/release/ximea_camera
```

**Config files missing:**
```bash
cp config.example.toml config.toml
cp visual_stimuli.example.toml visual_stimuli.toml
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
- Are flies in the trigger zone? (check position vs. `radius` and `z_lim`)
- Is cooldown expired? (check time since last trigger)

Enable debug logging:
```toml
[logging]
level = "DEBUG"
```

**Visual stimuli not appearing:**
- Verify `enabled = true` in `visual_stimuli.toml`
- Test standalone: `python -m src.processes.visual --standalone`
- Check window appears (may be on a different screen)

**Camera not recording:**
```python
from src.processes.camera import check_camera_prerequisites
results = check_camera_prerequisites("config.toml")
print(results)
```

Common causes: camera not detected (`lsusb | grep Ximea`), binary not built, user not in `video` group, insufficient disk space.

**Video encoding slow:**
```bash
sudo ubuntu-drivers autoinstall
# Reboot, then verify:
ffmpeg -encoders | grep nvenc
```

**Liquid lens not responding:**
```bash
ls -l /dev/ttyUSB*
sudo chmod 666 /dev/ttyUSB1
ls calibrations/liquid_lens.csv
```

## Performance

**Visual stimuli FPS drops below 240Hz:**

Check logs for: `WARNING - Performance: 180.2 fps`

Causes:
- Shapes being created/deleted every frame instead of updated in-place
- Too many shapes in scene (>1000)
- Expensive computation in `update()` or `render()`

Fixes: Pre-calculate constants in `__init__()`, reduce shape count, use rectangles instead of circles.

**Camera frame drops:**
- Check CPU usage during recording
- Verify NVENC is active (check ffmpeg output in camera logs)
- Check disk I/O bandwidth

## Calibration

**Calibration window doesn't appear:**
- Check displays are configured and connected
- Verify `window_x_offset` in config matches your display setup

**Stimuli appear at wrong positions after calibration:**
- Re-run `--calibrate-mapping` with the tracked object more carefully positioned
- Ensure `use_empirical_calibration = true` in config
- Verify the object was pointing directly at each calibration circle (not just near it)

**Can't see tracking data in Braid:**
- Confirm cameras are running and calibrated in Braid
- Verify the tracked object is within the tracking volume
