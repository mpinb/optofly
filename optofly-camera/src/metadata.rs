use crate::buffer::FrameBuffer;
use std::fs::File;

/// Write per-frame metadata CSV matching the Python format:
/// frame_idx,nframe,ts_sec,ts_usec,cam_time_ns
pub fn write_csv(buffer: &FrameBuffer, csv_path: &str) -> Result<(), String> {
    let file = File::create(csv_path)
        .map_err(|e| format!("Cannot create CSV {}: {}", csv_path, e))?;
    let mut wtr = csv::Writer::from_writer(file);

    wtr.write_record(["frame_idx", "nframe", "ts_sec", "ts_usec", "cam_time_ns"])
        .map_err(|e| format!("CSV header error: {}", e))?;

    for (i, meta) in buffer.metadata().iter().enumerate() {
        wtr.write_record(&[
            i.to_string(),
            meta.nframe.to_string(),
            meta.ts_sec.to_string(),
            meta.ts_usec.to_string(),
            meta.cam_time_ns.to_string(),
        ])
        .map_err(|e| format!("CSV row error: {}", e))?;
    }

    wtr.flush().map_err(|e| format!("CSV flush error: {}", e))?;
    Ok(())
}
