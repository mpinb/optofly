/// Per-frame metadata stored alongside pixel data.
#[derive(Clone, Copy, Default)]
pub struct FrameMeta {
    pub nframe: u32,
    pub ts_sec: u32,
    pub ts_usec: u32,
    pub cam_time_ns: u64,
}

/// A linear buffer for grayscale camera frames.
///
/// Stores up to `capacity` frames of `frame_bytes` each in a contiguous
/// allocation. Frames are written sequentially from index 0.
pub struct FrameBuffer {
    data: Vec<u8>,
    meta: Vec<FrameMeta>,
    capacity: usize,
    frame_bytes: usize,
    /// Number of frames written (0..capacity).
    filled: usize,
}

impl FrameBuffer {
    pub fn new(capacity: usize, frame_bytes: usize) -> Self {
        FrameBuffer {
            data: vec![0u8; capacity * frame_bytes],
            meta: vec![FrameMeta::default(); capacity],
            capacity,
            frame_bytes,
            filled: 0,
        }
    }

    /// Returns a mutable slice for the next frame slot.
    /// Returns `None` if the buffer is full.
    pub fn next_slot(&mut self) -> Option<&mut [u8]> {
        if self.filled >= self.capacity {
            return None;
        }
        let start = self.filled * self.frame_bytes;
        Some(&mut self.data[start..start + self.frame_bytes])
    }

    /// Record metadata for the current slot and advance the write index.
    pub fn commit(&mut self, meta: FrameMeta) {
        debug_assert!(self.filled < self.capacity);
        self.meta[self.filled] = meta;
        self.filled += 1;
    }

    /// Reset the buffer for reuse (does not deallocate).
    pub fn reset(&mut self) {
        self.filled = 0;
    }

    /// Number of valid frames currently stored.
    pub fn filled(&self) -> usize {
        self.filled
    }

    #[allow(dead_code)]
    pub fn capacity(&self) -> usize {
        self.capacity
    }

    #[allow(dead_code)]
    pub fn frame_bytes(&self) -> usize {
        self.frame_bytes
    }

    /// Whether the buffer is at capacity.
    pub fn is_full(&self) -> bool {
        self.filled >= self.capacity
    }

    /// Get metadata slice for the first `filled` frames.
    pub fn metadata(&self) -> &[FrameMeta] {
        &self.meta[..self.filled]
    }

    /// Write all filled frames to a writer (e.g. ffmpeg stdin pipe).
    /// Single contiguous write — no ring-buffer reordering needed.
    pub fn write_to<W: std::io::Write>(&self, writer: &mut W) -> std::io::Result<()> {
        let byte_end = self.filled * self.frame_bytes;
        if byte_end > 0 {
            writer.write_all(&self.data[..byte_end])?;
        }
        Ok(())
    }
}
