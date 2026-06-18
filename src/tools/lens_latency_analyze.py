#!/usr/bin/env python3
"""Summarise lens-focusing latency from one or more ``*_lens_timing.csv`` files.

Reads the per-trial CSVs written by :class:`LiquidLens` and prints percentile
breakdowns for each measurable stage of the focusing pipeline, plus a
recommended ``system_latency`` value to feed back into
``configs/config.toml`` under ``[liquid_lens.kalman]``.

Stages (computed only when both timestamps are populated):

* ``usb_ms``      = ``t_diopter_sent - t_serial_start`` (USB serial write)
* ``pubsub_ms``   = ``t_serial_start - t_relay``        (BraidPublisher → LiquidLens)
* ``software_ms`` = ``t_diopter_sent - t_relay``          (end-to-end software path)

Run with ``python -m src.tools.lens_latency_analyze /mnt/data/videos/<braid_dir>``.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from typing import Iterable, List, Optional


PERCENTILES = (50, 90, 95, 99)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarise lens latency from *_lens_timing.csv files.",
    )
    parser.add_argument(
        "path",
        help=(
            "Path to a single *_lens_timing.csv file, a directory containing "
            "such files, or a shell glob pattern (quote it)."
        ),
    )
    parser.add_argument(
        "--lens-settle-ms",
        type=float,
        default=10.0,
        help=(
            "Estimated mechanical settling time of the Optotune lens in ms. "
            "Added to the measured software delay to recommend system_latency. "
            "Check your lens datasheet (typical EL-10-30: 5-15 ms)."
        ),
    )
    parser.add_argument(
        "--recommend-percentile",
        type=int,
        default=90,
        choices=[50, 90, 95, 99],
        help=(
            "Percentile of software_ms used to recommend system_latency. "
            "Higher = lens leads correctly more often, at the cost of "
            "occasional small overshoot."
        ),
    )
    parser.add_argument(
        "--config",
        default="configs/config.toml",
        help=(
            "Path to config.toml; if readable, the current system_latency is "
            "displayed alongside the recommendation. Pass empty string to skip."
        ),
    )
    return parser.parse_args()


def expand_csv_paths(path: str) -> List[str]:
    """Resolve ``path`` (file, dir, or glob) into a sorted list of CSV files."""
    if os.path.isfile(path):
        return [path]

    if os.path.isdir(path):
        candidates = sorted(glob.glob(os.path.join(path, "*_lens_timing.csv")))
        if not candidates:
            candidates = sorted(
                glob.glob(os.path.join(path, "**", "*_lens_timing.csv"), recursive=True)
            )
        return candidates

    return sorted(glob.glob(path))


def _maybe_float(value: str) -> Optional[float]:
    if value == "" or value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def collect_stage_samples(
    csv_paths: Iterable[str],
) -> tuple[dict[str, List[float]], int, int]:
    """Walk every CSV row and gather per-stage delays in milliseconds.

    Returns the per-stage sample dict, the total row count, and the count of
    rows where ``t_relay`` was populated.
    """
    samples: dict[str, List[float]] = {"usb_ms": [], "pubsub_ms": [], "software_ms": []}
    total = 0
    relay_filled = 0

    for path in csv_paths:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                t_relay = _maybe_float(row.get("t_relay", ""))
                t_serial_start = _maybe_float(row.get("t_serial_start", ""))
                t_diopter_sent = _maybe_float(row.get("t_diopter_sent", ""))

                if t_serial_start is not None and t_diopter_sent is not None:
                    samples["usb_ms"].append((t_diopter_sent - t_serial_start) * 1000.0)

                if t_relay is not None:
                    relay_filled += 1
                    if t_serial_start is not None:
                        samples["pubsub_ms"].append((t_serial_start - t_relay) * 1000.0)
                    if t_diopter_sent is not None:
                        samples["software_ms"].append(
                            (t_diopter_sent - t_relay) * 1000.0
                        )

    return samples, total, relay_filled


def percentile(data: List[float], pct: float) -> float:
    """Linear-interpolated percentile (no numpy/statistics dependency)."""
    if not data:
        raise ValueError("empty data")
    sorted_data = sorted(data)
    if len(sorted_data) == 1:
        return sorted_data[0]
    rank = (pct / 100.0) * (len(sorted_data) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_data) - 1)
    weight = rank - lower
    return sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight


def format_stage_row(name: str, data: List[float]) -> str:
    if not data:
        return f"  {name:<14} (no data)"
    parts = [f"n={len(data):>6}"]
    for p in PERCENTILES:
        parts.append(f"p{p}={percentile(data, p):7.3f}")
    parts.append(f"max={max(data):7.3f}")
    return f"  {name:<14} " + "  ".join(parts) + "  ms"


def read_current_system_latency(config_path: str) -> Optional[float]:
    if not config_path or not os.path.isfile(config_path):
        return None
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:  # pragma: no cover
            import tomli as tomllib  # type: ignore
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None
    kalman = data.get("liquid_lens", {}).get("kalman", {})
    val = kalman.get("system_latency")
    return float(val) if val is not None else None


def main() -> None:
    args = parse_args()
    csv_paths = expand_csv_paths(args.path)
    if not csv_paths:
        print(f"No *_lens_timing.csv files matched: {args.path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {len(csv_paths)} CSV file(s) from: {args.path}")
    samples, total, relay_filled = collect_stage_samples(csv_paths)
    print(f"Total rows: {total}  (t_relay populated in {relay_filled})")
    print()

    print("Per-stage latency (milliseconds):")
    for stage in ("usb_ms", "pubsub_ms", "software_ms"):
        print(format_stage_row(stage, samples[stage]))
    print()

    software = samples["software_ms"]
    if not software:
        print(
            "End-to-end software latency unavailable: t_relay was empty in every row.",
            file=sys.stderr,
        )
        print(
            "Recommendation: redeploy the BraidPublisher t_relay fix, capture new "
            "recordings, then rerun this tool.",
            file=sys.stderr,
        )
        sys.exit(0)

    recommend_pct = args.recommend_percentile
    software_p = percentile(software, recommend_pct)
    settle = args.lens_settle_ms
    recommended_s = (software_p + settle) / 1000.0

    print("Recommended system_latency (seconds, for [liquid_lens.kalman]):")
    print(
        f"  software_p{recommend_pct} ({software_p:.3f} ms) "
        f"+ lens_settle ({settle:.3f} ms) = {recommended_s * 1000:.3f} ms "
        f"= {recommended_s:.4f} s"
    )

    current = read_current_system_latency(args.config)
    if current is not None:
        delta_ms = (recommended_s - current) * 1000
        print(f"  current configured value: {current:.4f} s  (Δ {delta_ms:+.3f} ms)")
    else:
        print(f"  (could not read current value from {args.config})")


if __name__ == "__main__":
    main()
