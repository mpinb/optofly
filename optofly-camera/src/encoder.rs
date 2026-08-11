use std::io::Read;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;

use crate::buffer::FrameBuffer;
use crate::metadata;

/// Payload sent from the capture thread to the encoder thread.
pub struct EncodeJob {
    pub buffer: FrameBuffer,
    pub base_name: String,
    pub fps: u32,
    pub width: u32,
    pub height: u32,
    pub trigger_frame_idx: Option<u64>,
    pub opto_frame_idx: Option<u64>,
    pub visual_frame_idx: Option<u64>,
}

fn detect_nvenc() -> bool {
    Command::new("ffmpeg")
        .args(["-hide_banner", "-encoders"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains("h264_nvenc"))
        .unwrap_or(false)
}

fn build_ffmpeg_cmd(
    video_path: &str,
    width: u32,
    height: u32,
    fps: u32,
    use_nvenc: bool,
) -> Command {
    let mut cmd = Command::new("ffmpeg");
    cmd.args([
        "-y",
        "-loglevel", "warning",
        "-thread_queue_size", "512",
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "-s", &format!("{}x{}", width, height),
        "-r", &fps.to_string(),
        "-i", "pipe:0",
    ]);

    if use_nvenc {
        cmd.args([
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-bf", "0",
            "-rc", "constqp",
            "-qp", "18",
            "-rc-lookahead", "32",
            "-spatial-aq", "1",
            "-pix_fmt", "nv12",
            "-profile:v", "high",
        ]);
    } else {
        cmd.args([
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "18",
        ]);
    }

    cmd.arg(video_path);
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    cmd
}

fn try_encode(job: &EncodeJob, video_path: &str, use_nvenc: bool) -> bool {
    let mut cmd = build_ffmpeg_cmd(video_path, job.width, job.height, job.fps, use_nvenc);
    let mut proc = match cmd.spawn() {
        Ok(p) => p,
        Err(e) => {
            log::error!("Failed to spawn ffmpeg: {}", e);
            return false;
        }
    };

    // Drain stderr on a separate thread while we write stdin below --
    // ffmpeg's stderr pipe has a small OS buffer (~64KB on Linux); if it
    // fills while we're still blocked writing multi-GB of frame data to
    // stdin, ffmpeg blocks writing stderr and we block writing stdin,
    // deadlocking both processes. Mirrors what Command::wait_with_output
    // does internally, but we can't use that here since we also need to
    // write to stdin ourselves.
    let mut stderr_pipe = proc.stderr.take();
    let stderr_thread = thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(ref mut stderr) = stderr_pipe {
            let _ = stderr.read_to_end(&mut buf);
        }
        buf
    });

    if let Some(ref mut stdin) = proc.stdin {
        if let Err(e) = job.buffer.write_to(stdin) {
            log::error!("Pipe write error: {}", e);
        }
    }
    drop(proc.stdin.take());

    let stderr_bytes = stderr_thread.join().unwrap_or_default();

    match proc.wait() {
        Ok(status) => {
            if !status.success() {
                let stderr = String::from_utf8_lossy(&stderr_bytes);
                log::error!("ffmpeg exited {}: {}", status, stderr.trim());
                false
            } else {
                true
            }
        }
        Err(e) => {
            log::error!("ffmpeg wait error: {}", e);
            false
        }
    }
}

/// Spawn the encoder thread. Returns a sender to submit encode jobs
/// and a receiver to reclaim spent buffers for reuse.
/// The thread runs until the job sender is dropped.
pub fn spawn() -> (mpsc::SyncSender<EncodeJob>, mpsc::Receiver<FrameBuffer>) {
    let (tx, rx) = mpsc::sync_channel::<EncodeJob>(2);
    let (buf_return_tx, buf_return_rx) = mpsc::channel::<FrameBuffer>();

    thread::Builder::new()
        .name("encoder".into())
        .spawn(move || {
            let mut use_nvenc = detect_nvenc();
            log::info!(
                "Encoder thread started: using {}",
                if use_nvenc { "h264_nvenc" } else { "libx264" }
            );

            while let Ok(job) = rx.recv() {
                let video_path = format!("{}.mp4", job.base_name);
                let csv_path = format!("{}.csv", job.base_name);
                let n = job.buffer.filled();
                log::info!("Encoding {} frames to {}", n, video_path);

                let t0 = std::time::Instant::now();

                let ok = try_encode(&job, &video_path, use_nvenc)
                    || if use_nvenc {
                        log::warn!("NVENC failed, retrying with libx264");
                        use_nvenc = false;
                        try_encode(&job, &video_path, false)
                    } else {
                        false
                    };

                if !ok {
                    log::error!("Encoding failed for {}, skipping", video_path);
                    // Still return the buffer even on failure
                    let _ = buf_return_tx.send(job.buffer);
                    continue;
                }

                if let Err(e) = metadata::write_csv(
                    &job.buffer,
                    &csv_path,
                    job.trigger_frame_idx,
                    job.opto_frame_idx,
                    job.visual_frame_idx,
                ) {
                    log::error!("CSV write failed: {}", e);
                }

                let elapsed = t0.elapsed().as_secs_f64();
                let size_mb = std::fs::metadata(&video_path)
                    .map(|m| m.len() as f64 / (1024.0 * 1024.0))
                    .unwrap_or(0.0);
                log::info!(
                    "Encode done: {:.1} MB, {:.2}s ({} fps encode), csv: {}",
                    size_mb, elapsed, (n as f64 / elapsed) as u64, csv_path,
                );

                // Return spent buffer for reuse
                let _ = buf_return_tx.send(job.buffer);
            }

            log::info!("Encoder thread exiting");
        })
        .expect("Failed to spawn encoder thread");

    (tx, buf_return_rx)
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::process::{Command, Stdio};
    use std::thread;

    /// Demonstrates the deadlock class fixed in try_encode: writing a large
    /// stdin payload to a child process before draining its stderr hangs
    /// once the child's stderr pipe buffer fills (~64KB on Linux) and the
    /// child blocks writing more stderr, no longer reading stdin. Uses a
    /// shell one-liner as a stand-in for ffmpeg so this test needs no
    /// hardware or real ffmpeg binary. If this test hangs (no output after
    /// ~10s), the concurrent-drain pattern below is broken -- Ctrl-C and
    /// investigate; do not add a timeout wrapper, external crates are out
    /// of scope for this fix.
    #[test]
    fn concurrent_stderr_drain_avoids_deadlock_on_large_stdin_and_stderr() {
        // Child: dump >64KB to stderr via the pipeline, then read+count stdin.
        let script = "yes X | head -c 200000 1>&2; wc -c";
        let mut child = Command::new("sh")
            .arg("-c")
            .arg(script)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("failed to spawn sh");

        // Drain stderr concurrently, same pattern as the try_encode fix.
        let mut stderr_pipe = child.stderr.take();
        let stderr_thread = thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(ref mut stderr) = stderr_pipe {
                let _ = stderr.read_to_end(&mut buf);
            }
            buf
        });

        // Write a payload past the pipe-buffer threshold to stdin.
        let payload = vec![b'a'; 200_000];
        if let Some(ref mut stdin) = child.stdin {
            stdin
                .write_all(&payload)
                .expect("stdin write must not block forever");
        }
        drop(child.stdin.take());

        let stderr_bytes = stderr_thread
            .join()
            .expect("stderr-draining thread must not panic");
        assert!(stderr_bytes.len() >= 200_000);

        let status = child.wait().expect("child must exit, not hang");
        assert!(status.success());
    }
}
