#!/usr/bin/env python3
"""Map an opto/visual/lens stimulus-onset Braid frame to the corresponding
recorded-video frame, for a completed recording.

Braid and the camera run on independent, unsynchronized frame counters (see
docs/camera.md), so there is no shared clock to look up a video frame by
timestamp. What we do have is exact: `latency.csv`'s `frame` field is the
Braid frame a system fired on, `record_frame` is the Braid frame recording
started on (== video frame 0, since the capture buffer resets at
`ZONE_ENTER`), and both counters run at their own ~nominal fps. So the
video frame is a fps-ratio scaling of the Braid-frame delta between the two
— an approximation that assumes no dropped frames on either side between
`record_frame` and `frame`, not an exact lookup.

Reads `latency.csv` and `config.toml` (for `camera.fps`) from the
recording, which may be either a raw `.braid` working folder (recording
still in progress, or a crashed/leftover one — see scripts/braidz_writer.py
to zip it first if you need the canonical layout) or the zipped `.braidz`
file Braid produces once a recording stops cleanly.

Run with:
    uv run python -m src.tools.frame_alignment /mnt/data/experiments/<ts>.braidz
    uv run python -m src.tools.frame_alignment /mnt/data/experiments/<ts>.braid

By default the videos folder is derived the same way `src/orchestration.py`
lays it out (`<data_root>/videos/<name>.braid`, a sibling of
`<data_root>/experiments/`); pass `--video-folder` to override.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

BRAID_FPS_DEFAULT = 100.0
DEFAULT_SYSTEMS = ("opto", "visual")


@dataclass
class AlignmentRow:
    obj_id: int
    system: str
    record_frame: int
    braid_frame: int
    braid_frame_delta: int
    video_frame: int
    video_csv: str
    video_frame_count: Optional[int]
    sham: bool
    latency_ms: Optional[float]

    @property
    def out_of_range(self) -> bool:
        return self.video_frame_count is not None and not (
            0 <= self.video_frame < self.video_frame_count
        )


def _is_zip(path: Path) -> bool:
    return path.is_file() and zipfile.is_zipfile(path)


def _read_member_text(path: Path, member: str) -> Optional[str]:
    """Read `member` from a .braidz zip or a raw .braid folder. None if absent."""
    if _is_zip(path):
        with zipfile.ZipFile(path) as zf:
            try:
                return zf.read(member).decode("utf-8")
            except KeyError:
                return None
    member_path = path / member
    return member_path.read_text() if member_path.is_file() else None


def load_latency_rows(path: Path) -> list[dict]:
    text = _read_member_text(path, "latency.csv")
    if text is None:
        raise FileNotFoundError(
            f"No latency.csv found in {path} — was LatencyLogger running for "
            "this recording?"
        )
    return list(csv.DictReader(io.StringIO(text)))


def load_camera_fps(path: Path) -> Optional[float]:
    """Read camera.fps from the config.toml copied into the recording, if any."""
    text = _read_member_text(path, "config.toml")
    if text is None:
        return None
    fps = tomllib.loads(text).get("camera", {}).get("fps")
    return float(fps) if fps is not None else None


def derive_video_folder(braid_path: Path) -> Path:
    """Mirror src/orchestration.py: <data_root>/videos/<name>.braid, a
    sibling of <data_root>/experiments/ (whichever folder braid_path is in)."""
    stem = (
        braid_path.stem
        if braid_path.suffix in (".braid", ".braidz")
        else braid_path.name
    )
    return braid_path.parent.parent / "videos" / f"{stem}.braid"


def compute_video_frame(
    braid_frame: int, record_frame: int, camera_fps: float, braid_fps: float
) -> tuple[int, int]:
    delta = braid_frame - record_frame
    video_frame = round(delta * camera_fps / braid_fps)
    return delta, video_frame


def count_video_frames(
    video_folder: Path, obj_id: str, record_frame: str
) -> tuple[str, Optional[int]]:
    csv_name = f"obj_id_{obj_id}_frame_{record_frame}.csv"
    csv_path = video_folder / csv_name
    if not csv_path.is_file():
        return csv_name, None
    with open(csv_path, newline="") as f:
        return csv_name, sum(1 for _ in csv.DictReader(f))


def build_alignment(
    braid_path: Path,
    video_folder: Path,
    camera_fps: float,
    braid_fps: float,
    systems: tuple[str, ...],
) -> list[AlignmentRow]:
    rows = []
    for raw in load_latency_rows(braid_path):
        if raw.get("system") not in systems:
            continue
        frame, record_frame = raw.get("frame"), raw.get("record_frame")
        if frame in (None, "") or record_frame in (None, ""):
            continue
        frame, record_frame = int(frame), int(record_frame)

        delta, video_frame = compute_video_frame(
            frame, record_frame, camera_fps, braid_fps
        )
        csv_name, frame_count = count_video_frames(
            video_folder, raw["obj_id"], str(record_frame)
        )
        latency_ms = raw.get("latency_ms")
        rows.append(
            AlignmentRow(
                obj_id=int(raw["obj_id"]),
                system=raw["system"],
                record_frame=record_frame,
                braid_frame=frame,
                braid_frame_delta=delta,
                video_frame=video_frame,
                video_csv=csv_name,
                video_frame_count=frame_count,
                sham=raw.get("sham", "").strip().lower() == "true",
                latency_ms=float(latency_ms) if latency_ms not in (None, "") else None,
            )
        )
    return rows


FIELDNAMES = [
    "obj_id",
    "system",
    "record_frame",
    "braid_frame",
    "braid_frame_delta",
    "video_frame",
    "video_csv",
    "video_frame_count",
    "sham",
    "latency_ms",
]


def write_rows(rows: list[AlignmentRow], out) -> None:
    writer = csv.DictWriter(out, fieldnames=FIELDNAMES)
    writer.writeheader()
    for row in rows:
        writer.writerow({name: getattr(row, name) for name in FIELDNAMES})


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "braid_path",
        type=Path,
        help="Path to a .braidz file or a raw .braid recording folder.",
    )
    parser.add_argument(
        "--video-folder",
        type=Path,
        default=None,
        help="Videos folder for this recording. Auto-derived if omitted.",
    )
    parser.add_argument(
        "--camera-fps",
        type=float,
        default=None,
        help="Override camera fps instead of reading it from the recording's "
        "config.toml (needed if the recording predates config-copying, or "
        "the file is missing/corrupt).",
    )
    parser.add_argument(
        "--braid-fps",
        type=float,
        default=BRAID_FPS_DEFAULT,
        help=f"Nominal Braid tracking rate in Hz (default: {BRAID_FPS_DEFAULT}).",
    )
    parser.add_argument(
        "--systems",
        default=",".join(DEFAULT_SYSTEMS),
        help="Comma-separated latency.csv systems to align "
        f"(default: {','.join(DEFAULT_SYSTEMS)}; also accepts 'lens').",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write CSV to this path instead of stdout.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    braid_path = args.braid_path

    if not braid_path.exists():
        print(f"Not found: {braid_path}", file=sys.stderr)
        return 1

    video_folder = args.video_folder or derive_video_folder(braid_path)
    if not video_folder.is_dir():
        print(
            f"Warning: video folder {video_folder} does not exist — "
            "video_frame_count will be blank for every row; pass "
            "--video-folder to point at the right directory.",
            file=sys.stderr,
        )

    camera_fps = args.camera_fps or load_camera_fps(braid_path)
    if camera_fps is None:
        print(
            "Could not determine camera fps from this recording's "
            "config.toml — pass --camera-fps explicitly.",
            file=sys.stderr,
        )
        return 1

    systems = tuple(s.strip() for s in args.systems.split(",") if s.strip())
    rows = build_alignment(
        braid_path, video_folder, camera_fps, args.braid_fps, systems
    )

    if not rows:
        print(
            f"No latency.csv rows found for systems={systems} in {braid_path}.",
            file=sys.stderr,
        )
        return 0

    if args.output:
        with open(args.output, "w", newline="") as f:
            write_rows(rows, f)
        print(f"Wrote {len(rows)} row(s) to {args.output}", file=sys.stderr)
    else:
        write_rows(rows, sys.stdout)

    for row in rows:
        if row.out_of_range:
            print(
                f"Warning: obj_id={row.obj_id} system={row.system} computed "
                f"video_frame={row.video_frame} is outside {row.video_csv}'s "
                f"{row.video_frame_count} recorded frames (recording likely "
                "ended — zone_timeout/buffer_full/left_fov — before the "
                "stimulus onset offset was reached).",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
