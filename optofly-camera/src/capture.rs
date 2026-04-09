use std::sync::mpsc::SyncSender;

use crate::buffer::{FrameBuffer, FrameMeta};
use crate::config::AppConfig;
use crate::encoder::{self, EncodeJob};

#[derive(Debug, PartialEq)]
enum State {
    Idle,
    Recording,
}

pub fn run(cfg: AppConfig) -> Result<(), String> {
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
    log::info!("Camera opened");

    cam.set_exposure(cfg.exposure_us)
        .map_err(|e| format!("Set exposure error: {}", e))?;

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

    let mut buffers = [
        FrameBuffer::new(buf_capacity, frame_bytes),
        FrameBuffer::new(buf_capacity, frame_bytes),
    ];
    let mut active_idx: usize = 0;

    // --- Encoder thread ---
    let encoder_tx: SyncSender<EncodeJob> = encoder::spawn();

    // --- Capture state ---
    let mut state = State::Idle;
    let mut recording_obj_id: u64 = 0;
    let mut recording_frame: u64 = 0;
    let mut dropped: u64 = 0;
    let mut prev_nframe: Option<u32> = None;
    let mut total_frames: u64 = 0;

    // --- Start acquisition (consumes cam, returns AcquisitionBuffer) ---
    let acq = cam
        .start_acquisition()
        .map_err(|e| format!("Start acquisition error: {}", e))?;
    log::info!("Acquisition started — entering capture loop");

    let mut should_exit = false;

    loop {
        if should_exit {
            break;
        }

        let img = match acq.next_image::<u8>(Some(5000)) {
            Ok(img) => img,
            Err(e) => {
                log::error!("get_image error: {}", e);
                continue;
            }
        };
        total_frames += 1;

        // Dropped frame tracking
        let nframe = img.nframe();
        if let Some(prev) = prev_nframe {
            let gap = nframe.wrapping_sub(prev).wrapping_sub(1);
            if gap > 0 && gap < 10000 {
                dropped += gap as u64;
            }
        }
        prev_nframe = Some(nframe);

        match state {
            State::Idle => {
                // Poll ZMQ for ZONE_ENTER or kill
                if let Ok(parts) = zmq_sub.recv_multipart(zmq::DONTWAIT) {
                    if !parts.is_empty() {
                        let topic = String::from_utf8_lossy(&parts[0]);
                        if topic == "kill" {
                            log::info!("Received kill signal");
                            should_exit = true;
                        } else if topic.as_ref() == cfg.zmq_zone_enter_topic
                            && parts.len() >= 2
                        {
                            let msg: serde_json::Value =
                                serde_json::from_slice(&parts[1]).unwrap_or_default();
                            recording_obj_id = msg["obj_id"].as_u64().unwrap_or(0);
                            recording_frame = msg["frame"].as_u64().unwrap_or(0);
                            buffers[active_idx].reset();
                            state = State::Recording;
                            log::info!(
                                "ZONE_ENTER obj_id={} — started recording (max {} frames)",
                                recording_obj_id,
                                buf_capacity
                            );
                        }
                    }
                }
            }

            State::Recording => {
                // Write frame into linear buffer
                if let Some(slot) = buffers[active_idx].next_slot() {
                    let data = img.data();
                    let copy_len = slot.len().min(data.len());
                    slot[..copy_len].copy_from_slice(&data[..copy_len]);

                    let ts_raw = img.timestamp_raw();
                    let ts_sec = (ts_raw >> 32) as u32;
                    let ts_usec = (ts_raw & 0xFFFF_FFFF) as u32;
                    buffers[active_idx].commit(FrameMeta {
                        nframe,
                        ts_sec,
                        ts_usec,
                        cam_time_ns: ts_sec as u64 * 1_000_000_000 + ts_usec as u64 * 1000,
                    });
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
                                dropped,
                            );
                            log::info!("Received kill signal during recording");
                            should_exit = true;
                        } else if topic.as_ref() == cfg.zmq_zone_exit_topic && parts.len() >= 2 {
                            let msg: serde_json::Value =
                                serde_json::from_slice(&parts[1]).unwrap_or_default();
                            if msg["obj_id"].as_u64().unwrap_or(0) == recording_obj_id {
                                log::info!(
                                    "ZONE_EXIT obj_id={} reason={} — stopping recording",
                                    recording_obj_id,
                                    msg["reason"].as_str().unwrap_or("unknown"),
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
                                    dropped,
                                );
                            }
                        }
                    }
                }

                // Safety: buffer full
                if state == State::Recording && buffers[active_idx].is_full() {
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
                        dropped,
                    );
                }
            }
        }
    }

    // Flush any active recording
    if state == State::Recording && buffers[active_idx].filled() > 0 {
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
            dropped,
        );
    }

    drop(encoder_tx); // Signal encoder thread to exit

    // stop_acquisition consumes acq and returns Camera, which is then dropped
    let _cam = acq
        .stop_acquisition()
        .map_err(|e| format!("Stop acquisition error: {}", e))?;

    log::info!(
        "Camera stopped. Total frames: {}, dropped: {}",
        total_frames,
        dropped
    );
    Ok(())
}

fn finish_recording(
    buffers: &mut [FrameBuffer; 2],
    active_idx: &mut usize,
    state: &mut State,
    encoder_tx: &SyncSender<EncodeJob>,
    obj_id: u64,
    frame: u64,
    save_folder: &str,
    fps: u32,
    width: u32,
    height: u32,
    dropped: u64,
) {
    let n_filled = buffers[*active_idx].filled();
    if n_filled == 0 {
        log::warn!("Recording ended with 0 frames, skipping encode");
        *state = State::Idle;
        return;
    }

    let base_name = format!("{}/obj_id_{}_frame_{}", save_folder, obj_id, frame);

    // Take the active buffer out and replace with a fresh one of same dimensions
    let cap = buffers[*active_idx].capacity();
    let fb = buffers[*active_idx].frame_bytes();
    let completed = std::mem::replace(&mut buffers[*active_idx], FrameBuffer::new(cap, fb));

    match encoder_tx.try_send(EncodeJob {
        buffer: completed,
        base_name,
        fps,
        width,
        height,
    }) {
        Ok(()) => {}
        Err(std::sync::mpsc::TrySendError::Full(_)) => {
            log::warn!("Encoder busy, skipping this recording");
        }
        Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
            log::error!("Encoder thread died");
        }
    }

    // Swap to standby buffer
    *active_idx = 1 - *active_idx;
    buffers[*active_idx].reset();
    *state = State::Idle;
    log::info!(
        "Recording done: {} frames, back to IDLE (dropped so far: {})",
        n_filled,
        dropped
    );
}
