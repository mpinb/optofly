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

    if let Some(ref mut stdin) = proc.stdin {
        if let Err(e) = job.buffer.write_to(stdin) {
            log::error!("Pipe write error: {}", e);
        }
    }
    drop(proc.stdin.take());

    match proc.wait_with_output() {
        Ok(output) => {
            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                log::error!("ffmpeg exited {}: {}", output.status, stderr.trim());
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

/// Spawn the encoder thread. Returns a sender to submit encode jobs.
/// The thread runs until the sender is dropped.
pub fn spawn() -> mpsc::SyncSender<EncodeJob> {
    let (tx, rx) = mpsc::sync_channel::<EncodeJob>(2);

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
                    continue;
                }

                if let Err(e) = metadata::write_csv(&job.buffer, &csv_path) {
                    log::error!("CSV write failed: {}", e);
                }

                if let Err(e) = metadata::save_debug_histogram(&job.buffer, &job.base_name) {
                    log::warn!("Debug histogram failed (non-fatal): {}", e);
                }

                let elapsed = t0.elapsed().as_secs_f64();
                let size_mb = std::fs::metadata(&video_path)
                    .map(|m| m.len() as f64 / (1024.0 * 1024.0))
                    .unwrap_or(0.0);
                log::info!(
                    "Encode done: {:.1} MB, {:.2}s ({} fps encode), csv: {}",
                    size_mb, elapsed, (n as f64 / elapsed) as u64, csv_path,
                );
            }

            log::info!("Encoder thread exiting");
        })
        .expect("Failed to spawn encoder thread");

    tx
}
