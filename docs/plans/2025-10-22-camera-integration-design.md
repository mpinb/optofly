# Camera Integration Design

**Date:** 2025-10-22
**Status:** Approved
**Author:** Claude Code (with user feedback)

## Overview

This design improves the existing Rust `ximea_camera` program and integrates it into the OptoFly Python system as a managed subprocess. The focus is on performance optimization, better error handling, and seamless integration with the existing ZMQ-based trigger system.

## Goals

- **(A)** Improve the Rust camera system: performance, logging, error handling
- **(B)** Integrate as Python-managed subprocess using WorkerProcess pattern
- Read camera configuration from `config.toml`
- Handle high-speed capture at 500fps × 2016×2016 without frame drops

## Architecture

### Multi-Process Design

The Rust system uses **3 separate processes** for isolation and performance:

```
Process 1: Camera Reader
  ↓ (shared memory ring buffer)
Process 2: Buffer Manager
  ↓ (channel: frame packets)
Process 3: Video Writer
```

**Rationale:** At 500fps × 2016×2016, raw data rate is ~2GB/sec. Process isolation prevents any bottleneck (encoding, disk I/O) from blocking camera capture.

### Process Responsibilities

1. **Camera Reader**
   - Tight capture loop: read frame → write to ring buffer
   - Never blocks on I/O
   - Target: <0.1ms per frame
   - Detects frame drops and camera disconnects

2. **Buffer Manager**
   - Maintains circular buffer of recent frames
   - Subscribes to ZMQ TRIGGER messages (non-blocking poll)
   - On trigger: collects [n_before + n_after] frame window
   - Sends owned packet to Video Writer
   - Target: <1ms trigger response

3. **Video Writer**
   - Receives frame packets via channel
   - Encodes to H264 video using FFmpeg
   - Writes CSV metadata
   - Isolated: slow encoding won't affect capture

### Data Structures

**Ring Buffer:**
```rust
struct FrameSlot {
    data: [u8; 2016 * 2016],    // Raw pixels
    metadata: FrameMetadata,     // timestamp, frame#, exposure
    sequence_num: AtomicU64,
}

struct RingBuffer {
    slots: [FrameSlot; BUFFER_SIZE],
    write_index: AtomicUsize,
}
```

**Buffer size calculation:**
```
buffer_size = (pre_trigger_time + post_trigger_time) × fps
Example: (0.5 + 1.5) × 500 = 1000 frames = ~4GB
```

**Messages:**
```rust
// ZMQ TRIGGER message (from trigger_handler.py)
struct TriggerMessage {
    obj_id: u32,
    frame: u64,
    // Ignore extra fields (timestamps, heading)
}

// Inter-process communication
enum Command {
    Trigger { obj_id: u32, frame: u64 },
    Shutdown,
}

struct VideoPacket {
    obj_id: u32,
    trigger_frame: u64,
    frames: Vec<(ImageData, FrameMetadata)>,
}
```

## Data Flow

### Normal Operation

1. Camera Reader continuously writes frames to ring buffer (circular, oldest overwritten)
2. Buffer Manager tracks write position, maintains sliding window
3. ZMQ subscriber (in Buffer Manager) polls for TRIGGER messages

### Trigger Event

1. TRIGGER message arrives with `{obj_id, frame}`
2. Buffer Manager notes current position: frame N
3. Ring buffer already contains frames [N - n_before, N]
4. Continue capturing until frame [N + n_after]
5. Copy window [N - n_before, N + n_after] to owned VideoPacket
6. Send packet to Video Writer via channel
7. Resume normal operation immediately

### Video Output

1. Video Writer receives VideoPacket
2. Spawns FFmpeg process with H264_NVENC encoding
3. Pipes raw frames to FFmpeg stdin
4. Output: `{save_folder}/obj_id_{id}_frame_{frame}.mp4`
5. CSV metadata: `{save_folder}/obj_id_{id}_frame_{frame}.csv`

## Configuration

### config.toml

```toml
[camera]
resolution = [2016, 2016]
fps = 500
exposure_time = 2000
pre_trigger_time = 0.5    # seconds
post_trigger_time = 1.5   # seconds

[camera.advanced]
serial = 0                # Camera serial (0 = first)
offset_x = 1056
offset_y = 170
```

### Runtime Arguments

Python launcher provides output directory:
```bash
python main.py --output-dir experiments/2025-10-22_trial5
```

### CLI Arguments to Rust

```bash
ximea_camera \
  --fps 500 \
  --width 2016 \
  --height 2016 \
  --exposure 2000 \
  --offset-x 1056 \
  --offset-y 170 \
  --serial 0 \
  --t-before 0.5 \
  --t-after 1.5 \
  --sub-port 5556 \
  --save-folder experiments/2025-10-22_trial5
```

## Python Integration

### CameraProcess Wrapper

```python
class CameraProcess(WorkerProcess):
    """Manages Rust camera subprocess."""

    def __init__(self, config_path="config.toml", output_dir=None,
                 event=None, process_name="Camera",
                 log_level="INFO", log_color="GREEN"):
        super().__init__(event, log_level, log_color, process_name)
        self.config = ConfigBase(config_path)._load_config()
        self.camera_config = self.config.get("camera", {})
        self.output_dir = output_dir  # Required
        self.rust_process = None

    def _build_command(self) -> list[str]:
        """Build CLI arguments from config."""
        cmd = ["./rust/ximea_camera/target/release/ximea_camera"]

        # Camera parameters
        cmd.extend(["--fps", str(self.camera_config.get("fps", 500))])
        cmd.extend(["--width", str(self.camera_config["resolution"][0])])
        cmd.extend(["--height", str(self.camera_config["resolution"][1])])
        cmd.extend(["--exposure", str(self.camera_config.get("exposure_time", 2000))])

        # Advanced settings
        advanced = self.camera_config.get("advanced", {})
        cmd.extend(["--serial", str(advanced.get("serial", 0))])
        cmd.extend(["--offset-x", str(advanced.get("offset_x", 1056))])
        cmd.extend(["--offset-y", str(advanced.get("offset_y", 170))])

        # Trigger timing
        cmd.extend(["--t-before", str(self.camera_config.get("pre_trigger_time", 0.5))])
        cmd.extend(["--t-after", str(self.camera_config.get("post_trigger_time", 1.5))])

        # ZMQ config
        zmq_config = self.config.get("zmq", {})
        cmd.extend(["--sub-port", str(zmq_config.get("trigger_port", 5556))])

        # Output directory (from runtime argument)
        cmd.extend(["--save-folder", self.output_dir])

        return cmd

    def run(self):
        """Main process loop."""
        self._initialize_logger()
        self.logger.info("Starting CameraProcess")

        cmd = self._build_command()
        self.logger.info(f"Launching Rust binary: {' '.join(cmd)}")

        # Launch subprocess
        self.rust_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1  # Line buffered
        )

        # Monitor output and forward to Python logs
        while not self.stop_event.is_set():
            line = self.rust_process.stdout.readline()
            if line:
                self.logger.info(f"[Rust] {line.strip()}")

            # Check if process died
            if self.rust_process.poll() is not None:
                self.logger.error("Rust process exited unexpectedly")
                break

        self._cleanup()

    def _cleanup(self):
        """Terminate Rust subprocess."""
        if self.rust_process and self.rust_process.poll() is None:
            self.logger.info("Terminating Rust subprocess")
            self.rust_process.terminate()
            self.rust_process.wait(timeout=5)
```

### Integration into OptoFly

CameraProcess is instantiated alongside other workers:
```python
# In main launcher
output_dir = args.output_dir  # From command line
camera = CameraProcess(config_path="config.toml", output_dir=output_dir, event=stop_event)
camera.start()
```

## Error Handling

### Camera Reader
- **Frame timeout:** Log error, attempt reconnect every 1s (max 10 attempts)
- **Frame drops:** Increment counter, log every 100 drops
- **Fatal error:** Exit with code 1 (Python can restart)

### Buffer Manager
- **ZMQ disconnect:** Reconnect with exponential backoff
- **Ring buffer overflow:** Log warning, continue
- **Video Writer channel full:** Drop oldest packet, log warning

### Video Writer
- **FFmpeg failure:** Log error, save CSV anyway, continue
- **Disk full:** Log critical, stop accepting packets
- **Corrupt frame data:** Skip video, log with frame numbers

## Logging and Monitoring

### Structured Logging

Using Rust `log` crate with `env_logger`:

```rust
// Startup
info!("Camera initialized: {}x{} @ {}fps", width, height, fps);
info!("Ring buffer: {} frames (~{} MB)", buffer_size, memory_mb);

// Performance (every 1000 frames)
debug!("Captured 1000 frames in {:.2}s, {} drops", duration, drops);

// Trigger events
info!("TRIGGER received: obj_id={}, frame={}", obj_id, frame);
info!("Video saved: obj_id_{}_frame_{}.mp4", obj_id, frame);

// Errors
warn!("Video Writer queue full, dropping packet for obj_id={}", obj_id);
error!("Camera timeout after {}ms, reconnecting", timeout);
```

### Metrics

- **Stdout:** Structured log lines (Python parses and forwards)
- **Heartbeat:** Frame count logged every second
- **Exit codes:** 0=clean, 1=camera error, 2=config error

## Performance Targets

- **Camera Reader:** <0.1ms per frame (2ms budget at 500fps)
- **Zero frame drops** during normal operation
- **Buffer Manager:** <1ms trigger response
- **Video Writer:** Isolated, encodes in background (~50-100ms per video)
- **Memory footprint:** ~4GB ring buffer for 1000 frames

## Design Principles

1. **Keep it simple:** No complex Rust patterns (async/await, lock-free algorithms)
2. **Process isolation:** Each process has independent memory, no shared bottlenecks
3. **Zero-copy where possible:** Ring buffer avoids allocations
4. **Fail gracefully:** Errors logged, system continues if possible
5. **Python-friendly:** Subprocess model, structured logging, CLI config

## Trade-offs

- **Memory usage:** ~4GB ring buffer (acceptable for performance)
- **Process overhead:** Multi-process adds complexity but provides isolation
- **CLI-only config:** No TOML parser in Rust (simpler, Python handles config)

## Integration with Existing System

### ZMQ Message Flow

```
Braid (port 5555)
  → trigger_handler.py
  → TRIGGER messages (port 5556)
  → Rust ximea_camera (subscribes)
```

**Message format (from trigger_handler.py):**
```json
{
    "obj_id": 123,
    "frame": 4567,
    "braid_timestamp": 1234.567,
    "trigger_timestamp": 1234.568,
    "mean_heading": 1.23
}
```

**Rust parsing:** Extract only `obj_id` and `frame`, ignore other fields.

### File Outputs

Videos and metadata saved to user-specified output directory:
- `{output_dir}/obj_id_123_frame_4567.mp4`
- `{output_dir}/obj_id_123_frame_4567.csv`

CSV format:
```csv
nframe,acq_nframe,timestamp_raw,exposure_time
1000,1000,123456789,2000
1001,1001,123458789,2000
...
```

## Next Steps

1. Write detailed implementation plan using `/superpowers:write-plan`
2. Set up git worktree for isolated development
3. Implement in phases:
   - Phase 1: Rust core (3 processes, ring buffer, basic capture)
   - Phase 2: Python wrapper (CameraProcess, config parsing)
   - Phase 3: Integration testing with trigger_handler
   - Phase 4: Performance tuning and optimization
