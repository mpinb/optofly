# Double Buffer Design for Ximea Camera

**This is a MUCH better design!** You've identified the core issue: the current approach fights Rust's ownership model with `UnsafeCell`, when we should work *with* it.

## Your Proposal (Double Buffer Ping-Pong)

```
Buffer A: [====k frames====]  ←  camera_reader writing (circular)
Buffer B: [====k frames====]     (idle)

TRIGGER arrives!
↓
Buffer A: [====k frames====]  →  sent to video_writer (owned exclusively)
Buffer B: [====k frames====]  ←  camera_reader switches here

video_writer encodes A (no races!)
↓
Buffer A: [====k frames====]  →  returned to pool (ready for next trigger)
Buffer B: [====k frames====]  ←  camera_reader still writing

Next TRIGGER arrives!
↓
Buffer B: [====k frames====]  →  sent to video_writer
Buffer A: [====k frames====]  ←  camera_reader switches back
```

## Advantages ✅

1. **No `unsafe` code needed!** - Ownership transfer = safe Rust
2. **No race conditions** - video_writer has exclusive ownership
3. **Simpler reasoning** - No atomic coordination needed
4. **Rust-idiomatic** - Uses channels and ownership, not raw pointers
5. **Actually safer** - Compiler enforces correctness

## Design Sketch

```rust
enum Buffer {
    A(Vec<FrameSlot>),
    B(Vec<FrameSlot>),
}

// Channels for ownership transfer
let (encode_tx, encode_rx) = channel::<Buffer>();  // To video_writer
let (return_tx, return_rx) = channel::<Buffer>();  // Back to pool

// Camera reader loop:
let mut active = Buffer::A(create_buffer(k));
let mut standby = Buffer::B(create_buffer(k));

loop {
    write_frame_to_buffer(&mut active);

    if trigger_received {
        encode_tx.send(active).unwrap();  // Move ownership to writer
        active = standby;                  // Switch to other buffer
        standby = return_rx.recv().unwrap(); // Get encoded buffer back
    }
}

// Video writer loop:
loop {
    let buffer = encode_rx.recv().unwrap();  // Receive owned buffer
    encode_video(&buffer);                    // Exclusive access!
    return_tx.send(buffer).unwrap();         // Return to pool
}
```

## Edge Cases to Handle

### 1. **Trigger while encoding previous buffer**
```rust
if trigger_received {
    if return_rx.try_recv().is_err() {
        log::warn!("Trigger dropped - still encoding previous buffer");
        continue;
    }
    // Safe to swap
}
```

With `min_trigger_interval = 10s` (from config), and encoding taking ~1s, this should be **extremely rare**.

### 2. **Startup state**
```rust
// Initially both buffers with camera_reader
let mut active = Buffer::A(...);
let mut standby = Buffer::B(...);

// After first trigger:
// - A goes to video_writer
// - B becomes active
// - When A returns, it becomes standby
```

### 3. **Shutdown**
```rust
// Send "poison pill" to video_writer
encode_tx.send(PoisonPill).unwrap();

// Wait for buffers to return
drop(encode_tx);
let _ = return_rx.recv();  // Get back whatever was being encoded
```

## Memory Trade-off

| Metric | Current | Double Buffer |
|--------|---------|---------------|
| Memory | 4 GB | **8 GB** |
| Safety | Unsafe | **Safe** |
| Race conditions | **Possible** | None |
| Code complexity | **High** (UnsafeCell) | Low (channels) |

For a system with sufficient RAM, this is **absolutely worth it**.

## Performance Impact

**Negligible!**
- Channel send/receive is ~100ns
- Happens once per trigger (every 10+ seconds)
- No continuous locking overhead
- Buffer swap is just pointer reassignment

## Alternative: Triple Buffering (If needed)

If triggers can arrive rapidly:
```rust
struct BufferPool {
    available: Receiver<Buffer>,
    in_use: Sender<Buffer>,
}

// camera_reader: always has at least one buffer
// video_writer: can queue multiple buffers
```

But with your `min_trigger_interval = 10s`, double buffering is sufficient.

## My Recommendation

**Do this refactor!** It's:
1. Safer (no unsafe code)
2. Simpler (easier to maintain)
3. More correct (no race conditions)
4. More Rust-idiomatic (ownership-based)
5. Memory cost is acceptable

The current design was premature optimization (trying to minimize memory at the cost of safety). Your double-buffer approach is the right trade-off.

## Implementation Complexity

**Much simpler than current!** You remove:
- `UnsafeCell` wrapper
- `unsafe impl Send/Sync`
- All `unsafe` blocks
- Complex atomic coordination

And replace with:
- Two buffers
- Two channels
- Simple swap logic

**I strongly support this redesign.**

## Detailed Implementation Plan

### Step 1: Redefine FrameSlot (No UnsafeCell)

```rust
pub struct FrameSlot {
    pub data: Vec<u8>,              // No UnsafeCell!
    pub metadata: FrameMetadata,    // No UnsafeCell!
}

// No unsafe impl Send/Sync needed - automatic!
```

### Step 2: Create Double Buffer Structure

```rust
pub struct FrameBuffer {
    slots: Vec<FrameSlot>,
    write_index: usize,  // Not atomic - single writer
    capacity: usize,
    width: usize,
    height: usize,
}

impl FrameBuffer {
    pub fn new(capacity: usize, width: usize, height: usize) -> Self {
        let slots = (0..capacity)
            .map(|_| FrameSlot::new(width, height))
            .collect();

        Self {
            slots,
            write_index: 0,
            capacity,
            width,
            height,
        }
    }

    pub fn write_frame(&mut self, frame_data: &[u8], metadata: FrameMetadata) {
        let slot = &mut self.slots[self.write_index];
        slot.data.copy_from_slice(frame_data);
        slot.metadata = metadata;

        self.write_index = (self.write_index + 1) % self.capacity;
    }

    pub fn get_frames(&self, start_offset: usize, count: usize) -> Vec<(Vec<u8>, FrameMetadata)> {
        let start_idx = if self.write_index >= start_offset {
            self.write_index - start_offset
        } else {
            self.capacity + self.write_index - start_offset
        };

        (0..count)
            .map(|i| {
                let idx = (start_idx + i) % self.capacity;
                let slot = &self.slots[idx];
                (slot.data.clone(), slot.metadata)
            })
            .collect()
    }
}
```

### Step 3: Message Types

```rust
pub enum CameraMessage {
    Buffer(FrameBuffer),
    Shutdown,
}

pub enum WriterMessage {
    Trigger { obj_id: u32, frame: u64 },
    Shutdown,
}
```

### Step 4: Camera Reader Process

```rust
pub fn camera_reader_process(
    mut cam: xiapi::Camera,
    trigger_rx: Receiver<WriterMessage>,
    buffer_tx: Sender<CameraMessage>,
    mut buffer_rx: Receiver<FrameBuffer>,
    buffer_size: usize,
    width: usize,
    height: usize,
) -> Result<(), Box<dyn std::error::Error>> {

    // Initialize double buffers
    let mut active = FrameBuffer::new(buffer_size, width, height);
    let mut standby = FrameBuffer::new(buffer_size, width, height);

    let buffer = cam.start_acquisition()?;

    loop {
        // Check for trigger (non-blocking)
        match trigger_rx.try_recv() {
            Ok(WriterMessage::Trigger { obj_id, frame }) => {
                // Try to get standby buffer back
                match buffer_rx.try_recv() {
                    Ok(returned_buffer) => {
                        // Send active buffer to encoder
                        buffer_tx.send(CameraMessage::Buffer(active))?;

                        // Swap buffers
                        active = standby;
                        standby = returned_buffer;

                        log::info!("Swapped buffers for trigger obj_id={}, frame={}", obj_id, frame);
                    }
                    Err(_) => {
                        log::warn!("Trigger dropped - no standby buffer available");
                    }
                }
            }
            Ok(WriterMessage::Shutdown) => break,
            Err(_) => {} // No message
        }

        // Capture frame
        let frame = buffer.next_image::<u8>(Some(5000))?;
        let metadata = FrameMetadata {
            nframe: frame.nframe(),
            acq_nframe: frame.acq_nframe(),
            timestamp_raw: frame.timestamp_raw(),
            exposure_time: frame.exposure_time_us(),
        };

        let img_buffer = ImageBuffer::<image::Luma<u8>, Vec<u8>>::from(frame);
        active.write_frame(img_buffer.as_raw(), metadata);
    }

    Ok(())
}
```

### Step 5: Video Writer Process

```rust
pub fn video_writer_process(
    buffer_rx: Receiver<CameraMessage>,
    buffer_return_tx: Sender<FrameBuffer>,
    save_folder: String,
    n_before: usize,
    n_after: usize,
) -> Result<(), Box<dyn std::error::Error>> {

    let save_path = Path::new(&save_folder);
    save_path.mkdir_all()?;

    loop {
        match buffer_rx.recv()? {
            CameraMessage::Buffer(buffer) => {
                // Extract frames (we have exclusive ownership!)
                let frames = buffer.get_frames(n_before, n_before + n_after);

                // Encode video
                encode_video(&frames, &save_path)?;

                // Return buffer to pool
                buffer_return_tx.send(buffer)?;
            }
            CameraMessage::Shutdown => break,
        }
    }

    Ok(())
}
```

### Step 6: Buffer Manager (Coordination)

```rust
pub fn buffer_manager_process(
    subscriber: zmq::Socket,
    trigger_tx: Sender<WriterMessage>,
    n_after: usize,
) -> Result<(), Box<dyn std::error::Error>> {

    let mut collecting_after = false;
    let mut trigger_obj_id: u32 = 0;
    let mut trigger_frame: u64 = 0;
    let mut frames_after_trigger = 0;

    loop {
        match subscriber.recv_string(zmq::DONTWAIT) {
            Ok(result) => match result {
                Ok(full_message) => {
                    let parts: Vec<&str> = full_message.splitn(2, ' ').collect();
                    if parts.len() == 2 && parts[0] == "TRIGGER" {
                        match serde_json::from_str::<TriggerMessage>(parts[1]) {
                            Ok(msg) => {
                                collecting_after = true;
                                trigger_frame = msg.frame;
                                trigger_obj_id = msg.obj_id;
                                frames_after_trigger = 0;
                            }
                            Err(e) => log::warn!("Parse error: {}", e),
                        }
                    } else if full_message == "kill" {
                        trigger_tx.send(WriterMessage::Shutdown)?;
                        break;
                    }
                }
                Err(_) => {}
            },
            Err(zmq::Error::EAGAIN) => {}
            Err(e) => log::error!("ZMQ error: {}", e),
        }

        if collecting_after {
            frames_after_trigger += 1;

            if frames_after_trigger >= n_after {
                trigger_tx.send(WriterMessage::Trigger {
                    obj_id: trigger_obj_id,
                    frame: trigger_frame,
                })?;
                collecting_after = false;
            }
        }

        std::thread::sleep(std::time::Duration::from_millis(1));
    }

    Ok(())
}
```

### Step 7: Main Orchestration

```rust
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();

    let buffer_size = args.calculate_buffer_size();
    let n_before = (args.t_before * args.fps) as usize;
    let n_after = (args.t_after * args.fps) as usize;

    // Channels for coordination
    let (trigger_tx, trigger_rx) = unbounded();           // buffer_manager → camera_reader
    let (buffer_tx, buffer_rx) = unbounded();             // camera_reader → video_writer
    let (buffer_return_tx, buffer_return_rx) = unbounded(); // video_writer → camera_reader

    let cam = initialize_camera(&args)?;
    let subscriber = connect_zmq_subscriber(&args.address, &args.sub_port)?;

    // Spawn threads
    let camera_thread = thread::spawn(move || {
        camera_reader_process(
            cam,
            trigger_rx,
            buffer_tx,
            buffer_return_rx,
            buffer_size,
            args.width as usize,
            args.height as usize,
        )
    });

    let buffer_thread = thread::spawn(move || {
        buffer_manager_process(subscriber, trigger_tx, n_after)
    });

    let writer_thread = thread::spawn(move || {
        video_writer_process(buffer_rx, buffer_return_tx, args.save_folder, n_before, n_after)
    });

    camera_thread.join().unwrap();
    buffer_thread.join().unwrap();
    writer_thread.join().unwrap();

    Ok(())
}
```

## Benefits Summary

1. **Zero unsafe code** - All safety guaranteed by Rust compiler
2. **No race conditions** - Ownership transfer prevents concurrent access
3. **Simpler code** - Channel-based coordination is idiomatic
4. **Better error handling** - Can propagate errors through Result types
5. **Easier to test** - No need to reason about memory ordering
6. **Easier to extend** - Adding triple buffering is straightforward

## Memory Cost

- Current: 4 GB (1 ring buffer)
- New: 8 GB (2 buffers)
- Trade-off: **Worth it for safety and simplicity**

## Next Steps

1. Implement new design in a feature branch
2. Test with simulated triggers
3. Benchmark performance (should be identical)
4. Migrate to production

**This is the right way to build this system in Rust!** 🚀
