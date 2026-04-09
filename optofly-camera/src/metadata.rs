use crate::buffer::FrameBuffer;
use std::fs::File;
use std::process::Command;

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

/// Generate a debug histogram PNG using a small inline Python script.
/// Falls back gracefully if Python/matplotlib is not available.
pub fn save_debug_histogram(buffer: &FrameBuffer, base_name: &str) -> Result<(), String> {
    let csv_path = format!("{}.csv", base_name);
    let png_path = format!("{}_debug.png", base_name);
    let _ = buffer.filled(); // used only to confirm buffer is valid

    let script = format!(
r#"
import sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

data = np.loadtxt('{csv_path}', delimiter=',', skiprows=1)
if data.shape[0] < 2:
    sys.exit(0)

nframes = data[:, 1]
ts_sec = data[:, 2]
ts_usec = data[:, 3]
cam_time_us = ts_sec * 1_000_000 + ts_usec
ifi_us = np.diff(cam_time_us)
nframe_diffs = np.diff(nframes)

fig, axes = plt.subplots(2, 2, figsize=(12, 8))
fig.suptitle('Capture debug diagnostics', fontsize=14)

def annotate(ax, diffs):
    ax.axvline(np.median(diffs), color='red', linestyle='--', label='median')
    stats = f"mean={{np.mean(diffs):.1f}}\nstd={{np.std(diffs):.1f}}\nmin={{np.min(diffs)}}\nmax={{np.max(diffs)}}"
    ax.text(0.97, 0.95, stats, transform=ax.transAxes, va='top', ha='right',
            fontsize=8, fontfamily='monospace', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))
    ax.legend(fontsize=8)

ax = axes[0, 0]
ax.hist(nframe_diffs, bins='auto', edgecolor='black', linewidth=0.5)
ax.set_title('Frame counter diff (expect all 1)')
ax.set_xlabel('nframe[i+1] - nframe[i]')
ax.set_ylabel('count')
annotate(ax, nframe_diffs)

ax = axes[0, 1]
ax.hist(ifi_us, bins='auto', edgecolor='black', linewidth=0.5)
ax.set_title('Inter-frame interval (us)')
ax.set_xlabel('us')
ax.set_ylabel('count')
annotate(ax, ifi_us)

ax = axes[1, 0]
median_ifi = np.median(ifi_us)
jitter = ifi_us - median_ifi
ax.hist(jitter, bins='auto', edgecolor='black', linewidth=0.5)
ax.set_title(f'Jitter (deviation from {{median_ifi:.0f}} us median)')
ax.set_xlabel('us')
ax.set_ylabel('count')
annotate(ax, jitter)

ax = axes[1, 1]
ax.plot(ifi_us, linewidth=0.5, alpha=0.7)
ax.axhline(median_ifi, color='red', linestyle='--', linewidth=1, label='median')
ax.set_title('Inter-frame interval over time')
ax.set_xlabel('frame index')
ax.set_ylabel('us')
ax.legend(fontsize=8)

fig.tight_layout()
fig.savefig('{png_path}', dpi=150)
plt.close(fig)
"#,
        csv_path = csv_path,
        png_path = png_path,
    );

    let output = Command::new("python3")
        .args(["-c", &script])
        .output()
        .map_err(|e| format!("Failed to run Python for histogram: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Histogram script failed: {}", stderr.trim()));
    }

    log::info!("Debug histogram: {}", png_path);
    Ok(())
}
