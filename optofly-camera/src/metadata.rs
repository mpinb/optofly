use crate::buffer::FrameBuffer;
use std::fs::File;

/// Write per-frame metadata CSV matching the Python format:
/// frame_idx,nframe,ts_sec,ts_usec,cam_time_ns,trigger_frame_idx,opto_frame_idx,visual_frame_idx
pub fn write_csv(
    buffer: &FrameBuffer,
    csv_path: &str,
    trigger_frame_idx: Option<u64>,
    opto_frame_idx: Option<u64>,
    visual_frame_idx: Option<u64>,
) -> Result<(), String> {
    let file = File::create(csv_path)
        .map_err(|e| format!("Cannot create CSV {}: {}", csv_path, e))?;
    let mut wtr = csv::Writer::from_writer(file);

    wtr.write_record([
        "frame_idx",
        "nframe",
        "ts_sec",
        "ts_usec",
        "cam_time_ns",
        "trigger_frame_idx",
        "opto_frame_idx",
        "visual_frame_idx",
    ])
    .map_err(|e| format!("CSV header error: {}", e))?;

    let tfi = trigger_frame_idx.map(|v| v.to_string()).unwrap_or_default();
    let ofi = opto_frame_idx.map(|v| v.to_string()).unwrap_or_default();
    let vfi = visual_frame_idx.map(|v| v.to_string()).unwrap_or_default();
    for (i, meta) in buffer.metadata().iter().enumerate() {
        wtr.write_record(&[
            i.to_string(),
            meta.nframe.to_string(),
            meta.ts_sec.to_string(),
            meta.ts_usec.to_string(),
            meta.cam_time_ns.to_string(),
            tfi.clone(),
            ofi.clone(),
            vfi.clone(),
        ])
        .map_err(|e| format!("CSV row error: {}", e))?;
    }

    wtr.flush().map_err(|e| format!("CSV flush error: {}", e))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::buffer::FrameMeta;

    #[test]
    fn write_csv_includes_opto_and_visual_frame_idx_columns() {
        let mut buf = FrameBuffer::new(2, 4);
        buf.commit(FrameMeta { nframe: 100, ts_sec: 1, ts_usec: 2, cam_time_ns: 3 });
        buf.commit(FrameMeta { nframe: 101, ts_sec: 1, ts_usec: 3, cam_time_ns: 4 });

        let path = std::env::temp_dir().join(format!(
            "optofly_camera_metadata_test_{}.csv",
            std::process::id()
        ));
        let path_str = path.to_string_lossy().to_string();

        write_csv(&buf, &path_str, Some(0), Some(1), None).expect("write_csv should succeed");

        let contents = std::fs::read_to_string(&path).expect("read back csv");
        std::fs::remove_file(&path).ok();

        let mut lines = contents.lines();
        assert_eq!(
            lines.next().unwrap(),
            "frame_idx,nframe,ts_sec,ts_usec,cam_time_ns,trigger_frame_idx,opto_frame_idx,visual_frame_idx"
        );
        assert_eq!(lines.next().unwrap(), "0,100,1,2,3,0,1,");
        assert_eq!(lines.next().unwrap(), "1,101,1,3,4,0,1,");
    }
}
