# Ximea Camera

High-speed triggered video recording system written in Rust.

**Specifications:**
- 500fps at 2016x2016 pixels
- H.264 encoding with NVENC hardware acceleration
- Double-buffer design for zero-copy, race-free operation
- ~10GB memory footprint (default settings)

## Architecture

Three concurrent processes coordinated via crossbeam channels:

1. **Camera Reader** (`camera_reader.rs`) — captures frames at 500fps, manages double-buffer ping-pong
2. **Buffer Manager** (`buffer_manager.rs`) — listens for ZMQ TRIGGER messages, coordinates timing
3. **Video Writer** (`video_writer.rs`) — receives owned buffers, encodes to MP4, returns buffers to pool

**Double-buffer pattern:** Camera Reader owns two `FrameBuffer` instances. On TRIGGER, the active buffer is transferred by ownership to Video Writer and the standby buffer becomes active. Video Writer has exclusive ownership during encoding — no concurrent access is possible. Compiler-verified with zero `unsafe` blocks.

**Memory:** `2 × (t_before + t_after + 0.5s safety) × fps × width × height`
Default settings: ~10GB.

## Building

```bash
cd rust/ximea_camera
cargo build --release
# Binary: target/release/ximea_camera
```

## Usage

**Via Python wrapper (normal use):**
```python
from src.processes.camera import CameraProcess
import multiprocessing as mp

stop_event = mp.Event()
camera = CameraProcess(config_path="configs/config.toml", event=stop_event)
if camera.initialize():
    camera.start()
    stop_event.set()
    camera.join()
```

**Pre-flight checks:**
```python
from src.processes.camera import check_camera_prerequisites
results = check_camera_prerequisites("configs/config.toml")
if not results["overall"]:
    for error in results["errors"]:
        print(error)
```

**Direct CLI:**
```bash
./ximea_camera \
    --fps 500 \
    --width 2016 \
    --height 2016 \
    --exposure 2000 \
    --offset-x 1056 \
    --offset-y 170 \
    --t-before 0.5 \
    --t-after 1.5 \
    --save-folder /path/to/videos
```

## ZMQ Protocol

Subscribes to topic `TRIGGER` on port 5556:
```
TRIGGER {"obj_id": 123, "frame": 4567}
```

Kill signal: `kill`

## Output

**Video:** `{save_folder}/obj_id_{obj_id}_frame_{frame}.mp4`
- Codec: H.264 (NVENC accelerated), grayscale 8-bit, 25fps playback

**Metadata CSV:** `{save_folder}/obj_id_{obj_id}_frame_{frame}.csv`
```csv
nframe,acq_nframe,timestamp_raw,exposure_time
100,100,123456789,2000
```

## Code Structure

```
rust/ximea_camera/src/
    main.rs          # Process orchestration, channel setup
    camera.rs        # Camera initialization and configuration
    camera_reader.rs # Frame capture loop, buffer ping-pong
    buffer_manager.rs # ZMQ subscriber, trigger logic, after-frame counting
    video_writer.rs  # FFmpeg encoding, CSV metadata, buffer return
    ring_buffer.rs   # Circular buffer implementation
    structs.rs       # Message types
    cli.rs           # Command-line argument parsing
```

## Troubleshooting

**Camera not detected:**
```bash
lsusb | grep Ximea
ldconfig -p | grep libxi
```

**NVENC not available** — FFmpeg falls back to software encoding:
```bash
sudo ubuntu-drivers autoinstall
ffmpeg -encoders | grep nvenc
```

**Binary not found:**
```bash
cd rust/ximea_camera && cargo build --release
```

**ZMQ port in use:**
```bash
netstat -tulpn | grep 5556
```

## Testing

```bash
# Unit tests (most require hardware)
cd rust/ximea_camera && cargo test

# Integration test (from repo root)
python tests/test_camera_integration.py
```
