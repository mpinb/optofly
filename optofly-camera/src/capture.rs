use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, SyncSender};
use std::sync::Arc;

use crate::buffer::{FrameBuffer, FrameMeta};
use crate::config::AppConfig;
use crate::encoder::{self, EncodeJob};

#[derive(Debug, PartialEq)]
enum State {
    Idle,
    Recording,
}

pub fn run(cfg: AppConfig) -> Result<(), String> {
    // --- SIGTERM handler ---
    let shutdown = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGTERM, Arc::clone(&shutdown))
        .map_err(|e| format!("Failed to register SIGTERM handler: {}", e))?;
    signal_hook::flag::register(signal_hook::consts::SIGINT, Arc::clone(&shutdown))
        .map_err(|e| format!("Failed to register SIGINT handler: {}", e))?;

    // --- ZMQ subscriber ---
    let zmq_ctx = zmq::Context::new();
    let zmq_sub = zmq_ctx
        .socket(zmq::SUB)
        .map_err(|e| format!("ZMQ socket error: {}", e))?;
    zmq_sub
        .connect(&cfg.zmq_trigger_address)
        .map_err(|e| format!("ZMQ connect error: {}", e))?;
    zmq_sub
        .set_subscribe(cfg.zmq_zone_enter_topic.as_bytes())
        .map_err(|e| format!("ZMQ subscribe error: {}", e))?;
    zmq_sub
        .set_subscribe(cfg.zmq_zone_exit_topic.as_bytes())
        .map_err(|e| format!("ZMQ subscribe error: {}", e))?;
    zmq_sub
        .set_subscribe(b"kill")
        .map_err(|e| format!("ZMQ subscribe error: {}", e))?;
    log::info!("ZMQ connected to {}", cfg.zmq_trigger_address);

    // --- Camera setup ---
    let mut cam = xiapi::open_device(None)
        .map_err(|e| format!("Cannot open XIMEA camera: {}", e))?;
    log::warn!("Camera opened");

    cam.set_exposure(cfg.exposure_us)
        .map_err(|e| format!("Set exposure error: {}", e))?;

    // Enable sensor corrections (matches Python CameraProcess behavior)
    // Note: BPC (bad pixel correction) is not exposed by the xiapi Rust crate;
    // the XIMEA SDK typically enables it by default.
    cam.set_column_fpn_correction(xiapi::XI_SWITCH::XI_ON)
        .map_err(|e| format!("Set column FPN correction error: {}", e))?;

    // Set ROI (centered on sensor)
    let sensor_w = cam.width().map_err(|e| format!("Get width error: {}", e))?;
    let sensor_h = cam
        .height()
        .map_err(|e| format!("Get height error: {}", e))?;
    let roi = xiapi::Roi {
        offset_x: (sensor_w - cfg.width) / 2,
        offset_y: (sensor_h - cfg.height) / 2,
        width: cfg.width,
        height: cfg.height,
    };
    let actual_roi = cam
        .set_roi(&roi)
        .map_err(|e| format!("Set ROI error: {}", e))?;

    cam.set_acq_timing_mode(xiapi::XI_ACQ_TIMING_MODE::XI_ACQ_TIMING_MODE_FRAME_RATE_LIMIT)
        .map_err(|e| format!("Set timing mode error: {}", e))?;
    cam.set_framerate(cfg.fps)
        .map_err(|e| format!("Set framerate error: {}", e))?;

    // Use unsafe buffer policy (zero-copy, API manages buffers) and increase
    // the internal queue depth so the SDK can absorb occasional processing
    // stalls without dropping frames.
    // XI_BP_UNSAFE = 0: zero-copy, API manages buffers directly
    cam.set_buffer_policy(0)
        .map_err(|e| format!("Set buffer policy error: {}", e))?;
    cam.set_buffers_queue_size(cfg.buffers_queue_size)
        .map_err(|e| format!("Set buffers queue size error: {}", e))?;
    cam.set_recent_frame(xiapi::XI_SWITCH::XI_OFF)
        .map_err(|e| format!("Set recent frame error: {}", e))?;
    log::info!(
        "Buffer policy: UNSAFE, queue size: {}, recent_frame: OFF",
        cfg.buffers_queue_size
    );

    let width = actual_roi.width;
    let height = actual_roi.height;
    let frame_bytes = (width * height) as usize;
    let fps = cfg.fps as u32;

    log::info!(
        "Sensor {}x{}, ROI {}x{}, offset ({}, {}), {} fps",
        sensor_w,
        sensor_h,
        width,
        height,
        actual_roi.offset_x,
        actual_roi.offset_y,
        fps
    );

    // --- Allocate double buffers ---
    let buf_capacity = cfg.buffer_capacity();
    log::info!(
        "Buffer: {} frames ({:.1}s, {:.0} MB x2)",
        buf_capacity,
        cfg.max_recording_time + 1.0,
        (buf_capacity * frame_bytes) as f64 / (1024.0 * 1024.0)
    );

    // Both buffers start as Some; the active one is taken when sent to encoder,
    // and restored when the encoder returns it via buffer_rx.
    let mut buffers: [Option<FrameBuffer>; 2] = [
        Some(FrameBuffer::new(buf_capacity, frame_bytes)),
        Some(FrameBuffer::new(buf_capacity, frame_bytes)),
    ];
    let mut active_idx: usize = 0;

    // --- Encoder thread ---
    let (encoder_tx, buffer_rx): (SyncSender<EncodeJob>, Receiver<FrameBuffer>) =
        encoder::spawn();

    // --- Capture state ---
    let mut state = State::Idle;
    let mut recording_obj_id: u64 = 0;
    let mut recording_frame: u64 = 0;
    let mut rec_dropped: u64 = 0;
    let mut rec_prev_nframe: Option<u32> = None;
    let mut total_frames: u64 = 0;
    // Frame index within the current recording buffer at which ZONE_ENTER fired.
    let mut trigger_frame_idx: Option<u64> = None;

    // --- Start acquisition (consumes cam, returns AcquisitionBuffer) ---
    let acq = cam
        .start_acquisition()
        .map_err(|e| format!("Start acquisition error: {}", e))?;
    log::info!("Acquisition started — entering capture loop");

    // Verify no row padding on first frame — flat copy assumes contiguous pixel data
    let first_img = acq
        .next_image::<u8>(Some(5000))
        .map_err(|e| format!("First frame error: {}", e))?;
    if first_img.data().len() != frame_bytes {
        return Err(format!(
            "Frame data size mismatch: expected {} ({}x{}), got {} — row padding detected. \
             Cannot use flat buffer copy.",
            frame_bytes, width, height, first_img.data().len()
        ));
    }
    drop(first_img);

    loop {
        // Check for SIGTERM/SIGINT
        if shutdown.load(Ordering::Relaxed) {
            log::info!("Received shutdown signal");
            break;
        }

        // Reclaim returned buffers from encoder (non-blocking)
        while let Ok(mut returned) = buffer_rx.try_recv() {
            returned.reset();
            // Put it back into whichever slot is empty
            if buffers[0].is_none() {
                buffers[0] = Some(returned);
            } else if buffers[1].is_none() {
                buffers[1] = Some(returned);
            } else {
                // Both slots occupied — shouldn't happen, but drop gracefully
                log::warn!("Returned buffer has no empty slot, dropping");
            }
        }

        let img = match acq.next_image::<u8>(Some(5000)) {
            Ok(img) => img,
            Err(e) => {
                log::error!("get_image error: {}", e);
                continue;
            }
        };
        total_frames += 1;
        let nframe = img.nframe();

        match state {
            State::Idle => {
                // Poll ZMQ for ZONE_ENTER or kill
                if let Ok(parts) = zmq_sub.recv_multipart(zmq::DONTWAIT) {
                    if !parts.is_empty() {
                        let topic = String::from_utf8_lossy(&parts[0]);
                        if topic == "kill" {
                            log::info!("Received kill signal");
                            break;
                        } else if topic.as_ref() == cfg.zmq_zone_enter_topic
                            && parts.len() >= 2
                        {
                            let msg: serde_json::Value =
                                serde_json::from_slice(&parts[1]).unwrap_or_default();
                            recording_obj_id = msg["obj_id"].as_u64().unwrap_or(0);
                            recording_frame = msg["frame"].as_u64().unwrap_or(0);
                            if let Some(ref mut buf) = buffers[active_idx] {
                                buf.reset();
                                rec_dropped = 0;
                                rec_prev_nframe = None;
                                trigger_frame_idx = Some(0);
                                state = State::Recording;
                                log::info!(
                                    "ZONE_ENTER obj_id={} — started recording (max {} frames)",
                                    recording_obj_id,
                                    buf_capacity
                                );
                            } else {
                                log::warn!(
                                    "ZONE_ENTER obj_id={} — no buffer available, skipping",
                                    recording_obj_id
                                );
                            }
                        }
                    }
                }
            }

            State::Recording => {
                // Per-video dropped frame tracking
                if let Some(prev) = rec_prev_nframe {
                    let gap = nframe.wrapping_sub(prev).wrapping_sub(1);
                    if gap > 0 && gap < 10000 {
                        rec_dropped += gap as u64;
                    }
                }
                rec_prev_nframe = Some(nframe);

                // Write frame into linear buffer
                if let Some(ref mut buf) = buffers[active_idx] {
                    if let Some(slot) = buf.next_slot() {
                        let data = img.data();
                        let copy_len = slot.len().min(data.len());
                        slot[..copy_len].copy_from_slice(&data[..copy_len]);

                        let ts_raw = img.timestamp_raw();
                        let ts_sec = (ts_raw >> 32) as u32;
                        let ts_usec = (ts_raw & 0xFFFF_FFFF) as u32;
                        let cam_time_ns = ts_sec as u64 * 1_000_000_000 + ts_usec as u64 * 1_000;
                        buf.commit(FrameMeta {
                            nframe,
                            ts_sec,
                            ts_usec,
                            cam_time_ns,
                        });
                    }
                }

                // Poll ZMQ for ZONE_EXIT or kill
                if let Ok(parts) = zmq_sub.recv_multipart(zmq::DONTWAIT) {
                    if !parts.is_empty() {
                        let topic = String::from_utf8_lossy(&parts[0]);
                        if topic == "kill" {
                            finish_recording(
                                &mut buffers,
                                &mut active_idx,
                                &mut state,
                                &encoder_tx,
                                recording_obj_id,
                                recording_frame,
                                &cfg.save_folder,
                                fps,
                                width,
                                height,
                                rec_dropped,
                                trigger_frame_idx,
                                "kill",
                            );
                            trigger_frame_idx = None;
                            log::info!("Received kill signal during recording");
                            break;
                        } else if topic.as_ref() == cfg.zmq_zone_exit_topic && parts.len() >= 2 {
                            let msg: serde_json::Value =
                                serde_json::from_slice(&parts[1]).unwrap_or_default();
                            if msg["obj_id"].as_u64().unwrap_or(0) == recording_obj_id {
                                let reason = msg["reason"].as_str().unwrap_or("unknown");
                                finish_recording(
                                    &mut buffers,
                                    &mut active_idx,
                                    &mut state,
                                    &encoder_tx,
                                    recording_obj_id,
                                    recording_frame,
                                    &cfg.save_folder,
                                    fps,
                                    width,
                                    height,
                                    rec_dropped,
                                    trigger_frame_idx,
                                    reason,
                                );
                                trigger_frame_idx = None;
                            }
                        }
                    }
                }

                // Safety: buffer full
                if state == State::Recording {
                    let is_full = buffers[active_idx]
                        .as_ref()
                        .map_or(false, |b| b.is_full());
                    if is_full {
                        log::warn!(
                            "Buffer full ({} frames), forcing recording stop",
                            buf_capacity
                        );
                        finish_recording(
                            &mut buffers,
                            &mut active_idx,
                            &mut state,
                            &encoder_tx,
                            recording_obj_id,
                            recording_frame,
                            &cfg.save_folder,
                            fps,
                            width,
                            height,
                            rec_dropped,
                            trigger_frame_idx,
                            "buffer_full",
                        );
                        trigger_frame_idx = None;
                    }
                }
            }
        }
    }

    // Flush any active recording
    let has_frames = buffers[active_idx]
        .as_ref()
        .map_or(false, |b| b.filled() > 0);
    if state == State::Recording && has_frames {
        finish_recording(
            &mut buffers,
            &mut active_idx,
            &mut state,
            &encoder_tx,
            recording_obj_id,
            recording_frame,
            &cfg.save_folder,
            fps,
            width,
            height,
            rec_dropped,
            trigger_frame_idx,
            "shutdown",
        );
    }

    drop(encoder_tx); // Signal encoder thread to exit

    // stop_acquisition consumes acq and returns Camera, which is then dropped
    let _cam = acq
        .stop_acquisition()
        .map_err(|e| format!("Stop acquisition error: {}", e))?;

    log::warn!("Camera stopped. Total frames: {}", total_frames);
    Ok(())
}

fn finish_recording(
    buffers: &mut [Option<FrameBuffer>; 2],
    active_idx: &mut usize,
    state: &mut State,
    encoder_tx: &SyncSender<EncodeJob>,
    obj_id: u64,
    frame: u64,
    save_folder: &str,
    fps: u32,
    width: u32,
    height: u32,
    rec_dropped: u64,
    trigger_frame_idx: Option<u64>,
    reason: &str,
) {
    let n_filled = buffers[*active_idx]
        .as_ref()
        .map_or(0, |b| b.filled());
    if n_filled == 0 {
        log::warn!("Recording ended with 0 frames, skipping encode");
        *state = State::Idle;
        return;
    }

    if let Err(e) = std::fs::create_dir_all(save_folder) {
        log::error!("Cannot create save folder {}: {}", save_folder, e);
        *state = State::Idle;
        return;
    }
    let base_name = format!("{}/obj_id_{}_frame_{}", save_folder, obj_id, frame);

    // Take the buffer out, leaving None in its slot.
    // The encoder will return it via the buffer_rx channel after encoding.
    let completed = buffers[*active_idx].take().unwrap();

    match encoder_tx.try_send(EncodeJob {
        buffer: completed,
        base_name: base_name.clone(),
        fps,
        width,
        height,
        trigger_frame_idx,
    }) {
        Ok(()) => {}
        Err(std::sync::mpsc::TrySendError::Full(job)) => {
            log::warn!(
                "Encoder busy, dropping recording obj_id={} frame={} ({} frames)",
                obj_id, frame, n_filled
            );
            // Put the buffer back so it's not lost
            buffers[*active_idx] = Some(job.buffer);
        }
        Err(std::sync::mpsc::TrySendError::Disconnected(job)) => {
            log::error!("Encoder thread died");
            buffers[*active_idx] = Some(job.buffer);
        }
    }

    // Swap to standby buffer
    *active_idx = 1 - *active_idx;
    if let Some(ref mut buf) = buffers[*active_idx] {
        buf.reset();
    }
    *state = State::Idle;

    let trigger_info = match trigger_frame_idx {
        Some(idx) => format!("trigger_frame={}", idx),
        None => "trigger_frame=none(no_zone_enter)".to_string(),
    };
    log::warn!(
        "Recording done: {} frames, {} dropped, {}, reason={}, back to IDLE",
        n_filled,
        rec_dropped,
        trigger_info,
        reason
    );
}
