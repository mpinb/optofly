#!/usr/bin/env python3
"""
Generate debug histograms from camera CSV metadata files.

Reads all .csv files in a folder (output from optofly-camera binary),
and generates a corresponding PNG histogram showing frame timing statistics.

Usage:
    python src/tools/generate_camera_histograms.py /path/to/videos/
    python src/tools/generate_camera_histograms.py /path/to/videos/ --output-suffix _analysis.png
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def generate_histogram(csv_path: Path, png_path: Path) -> bool:
    """
    Generate a debug histogram from a camera CSV metadata file.

    Returns True if successful, False otherwise.
    """
    try:
        data = np.loadtxt(csv_path, delimiter=",", skiprows=1)
        if data.shape[0] < 2:
            print(f"  {csv_path.name}: too few frames ({data.shape[0]}), skipping")
            return False

        nframes = data[:, 1]
        ts_sec = data[:, 2]
        ts_usec = data[:, 3]
        cam_time_us = ts_sec * 1_000_000 + ts_usec
        ifi_us = np.diff(cam_time_us)
        nframe_diffs = np.diff(nframes)

        fig, axes = plt.subplots(2, 2, figsize=(12, 8))
        fig.suptitle(f"Capture debug diagnostics ({csv_path.stem})", fontsize=14)

        def annotate(ax, diffs):
            ax.axvline(np.median(diffs), color="red", linestyle="--", label="median")
            stats = (
                f"mean={np.mean(diffs):.1f}\n"
                f"std={np.std(diffs):.1f}\n"
                f"min={np.min(diffs)}\n"
                f"max={np.max(diffs)}"
            )
            ax.text(
                0.97,
                0.95,
                stats,
                transform=ax.transAxes,
                va="top",
                ha="right",
                fontsize=8,
                fontfamily="monospace",
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
            )
            ax.legend(fontsize=8)

        ax = axes[0, 0]
        ax.hist(nframe_diffs, bins="auto", edgecolor="black", linewidth=0.5)
        ax.set_title("Frame counter diff (expect all 1)")
        ax.set_xlabel("nframe[i+1] - nframe[i]")
        ax.set_ylabel("count")
        annotate(ax, nframe_diffs)

        ax = axes[0, 1]
        ax.hist(ifi_us, bins="auto", edgecolor="black", linewidth=0.5)
        ax.set_title("Inter-frame interval (us)")
        ax.set_xlabel("us")
        ax.set_ylabel("count")
        annotate(ax, ifi_us)

        ax = axes[1, 0]
        median_ifi = np.median(ifi_us)
        jitter = ifi_us - median_ifi
        ax.hist(jitter, bins="auto", edgecolor="black", linewidth=0.5)
        ax.set_title(f"Jitter (deviation from {median_ifi:.0f} us median)")
        ax.set_xlabel("us")
        ax.set_ylabel("count")
        annotate(ax, jitter)

        ax = axes[1, 1]
        ax.plot(ifi_us, linewidth=0.5, alpha=0.7)
        ax.axhline(median_ifi, color="red", linestyle="--", linewidth=1, label="median")
        ax.set_title("Inter-frame interval over time")
        ax.set_xlabel("frame index")
        ax.set_ylabel("us")
        ax.legend(fontsize=8)

        fig.tight_layout()
        fig.savefig(png_path, dpi=150)
        plt.close(fig)

        print(f"  ✓ {png_path.name}")
        return True
    except Exception as e:
        print(f"  ✗ {csv_path.name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Generate debug histograms from camera CSV metadata files."
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Path to folder containing .csv files from optofly-camera",
    )
    parser.add_argument(
        "--output-suffix",
        default="_debug.png",
        help="Suffix for output PNG filenames (default: _debug.png)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List CSV files without generating histograms",
    )

    args = parser.parse_args()

    if not args.folder.is_dir():
        print(f"Error: {args.folder} is not a directory", file=sys.stderr)
        sys.exit(1)

    csv_files = sorted(args.folder.glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found in {args.folder}")
        sys.exit(0)

    print(f"Found {len(csv_files)} CSV file(s) in {args.folder}")

    if args.dry_run:
        for csv_path in csv_files:
            print(f"  {csv_path.name}")
        return

    print("\nGenerating histograms:")
    success_count = 0
    for csv_path in csv_files:
        png_path = csv_path.with_suffix(args.output_suffix)
        if generate_histogram(csv_path, png_path):
            success_count += 1

    print(f"\n{success_count}/{len(csv_files)} histograms generated successfully")
    sys.exit(0 if success_count == len(csv_files) else 1)


if __name__ == "__main__":
    main()
