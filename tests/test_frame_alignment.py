"""Tests for src.tools.frame_alignment.

No hardware, Braid, or camera required — everything here is file-format and
arithmetic logic exercised against synthetic recordings on disk.
"""

import csv
import zipfile
from pathlib import Path

import pytest

from src.tools.frame_alignment import (
    build_alignment,
    compute_video_frame,
    derive_video_folder,
    load_camera_fps,
    load_latency_rows,
)

LATENCY_FIELDS = [
    "obj_id",
    "frame",
    "record_frame",
    "system",
    "braid_timestamp",
    "trigger_timestamp",
    "activation_timestamp",
    "latency_ms",
    "sham",
]

CONFIG_TOML = """
[camera]
fps = 500
"""


def _write_latency_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LATENCY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _video_csv_rows(n: int) -> list[dict]:
    return [
        {
            "frame_idx": i,
            "nframe": 100 + i,
            "ts_sec": 0,
            "ts_usec": 0,
            "cam_time_ns": 0,
            "trigger_frame_idx": 0 if i == 0 else "",
        }
        for i in range(n)
    ]


def _write_video_csv(path: Path, n: int) -> None:
    rows = _video_csv_rows(n)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize(
    "braid_frame, record_frame, camera_fps, braid_fps, expected_delta, expected_video_frame",
    [
        (12345, 12345, 500.0, 100.0, 0, 0),
        (12350, 12345, 500.0, 100.0, 5, 25),
        (12280, 12280, 500.0, 100.0, 0, 0),
        # fps ratio rounds to nearest integer video frame
        (12347, 12345, 500.0, 100.0, 2, 10),
    ],
)
def test_compute_video_frame(
    braid_frame,
    record_frame,
    camera_fps,
    braid_fps,
    expected_delta,
    expected_video_frame,
):
    delta, video_frame = compute_video_frame(
        braid_frame, record_frame, camera_fps, braid_fps
    )
    assert delta == expected_delta
    assert video_frame == expected_video_frame


@pytest.mark.parametrize(
    "braid_path, expected",
    [
        (
            Path("/mnt/data/experiments/20260803_120000.braidz"),
            Path("/mnt/data/videos/20260803_120000.braid"),
        ),
        (
            Path("/mnt/data/experiments/20260803_120000.braid"),
            Path("/mnt/data/videos/20260803_120000.braid"),
        ),
    ],
)
def test_derive_video_folder_matches_orchestration_layout(braid_path, expected):
    assert derive_video_folder(braid_path) == expected


def _make_braid_folder(
    tmp_path: Path, latency_rows: list[dict], write_config: bool = True
) -> Path:
    braid_dir = tmp_path / "20260803_120000.braid"
    braid_dir.mkdir()
    _write_latency_csv(braid_dir / "latency.csv", latency_rows)
    if write_config:
        (braid_dir / "config.toml").write_text(CONFIG_TOML)
    return braid_dir


def _zip_folder(folder: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        for path in sorted(folder.rglob("*")):
            zf.write(path, arcname=path.relative_to(folder))
    return dest


def _opto_row(obj_id=1, frame=12350, record_frame=12345, sham=False, latency_ms=""):
    return {
        "obj_id": obj_id,
        "frame": frame,
        "record_frame": record_frame,
        "system": "opto",
        "braid_timestamp": 100.0,
        "trigger_timestamp": 100.0,
        "activation_timestamp": 100.01,
        "latency_ms": latency_ms,
        "sham": sham,
    }


def test_load_latency_rows_from_folder(tmp_path):
    braid_dir = _make_braid_folder(tmp_path, [_opto_row()])
    rows = load_latency_rows(braid_dir)
    assert len(rows) == 1
    assert rows[0]["system"] == "opto"


def test_load_latency_rows_from_zip(tmp_path):
    braid_dir = _make_braid_folder(tmp_path, [_opto_row()])
    braidz = _zip_folder(braid_dir, tmp_path / "20260803_120000.braidz")
    rows = load_latency_rows(braidz)
    assert len(rows) == 1
    assert rows[0]["system"] == "opto"


def test_load_latency_rows_missing_raises(tmp_path):
    empty_dir = tmp_path / "empty.braid"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        load_latency_rows(empty_dir)


def test_load_camera_fps_from_folder_and_zip(tmp_path):
    braid_dir = _make_braid_folder(tmp_path, [_opto_row()])
    assert load_camera_fps(braid_dir) == 500.0

    braidz = _zip_folder(braid_dir, tmp_path / "20260803_120000.braidz")
    assert load_camera_fps(braidz) == 500.0


def test_load_camera_fps_missing_config_returns_none(tmp_path):
    braid_dir = _make_braid_folder(tmp_path, [_opto_row()], write_config=False)
    assert load_camera_fps(braid_dir) is None


def test_build_alignment_joins_video_csv_and_flags_out_of_range(tmp_path):
    braid_dir = _make_braid_folder(
        tmp_path,
        [
            _opto_row(obj_id=1, frame=12350, record_frame=12345),
            _opto_row(obj_id=2, frame=99999, record_frame=99990, sham=True),
        ],
    )
    video_folder = tmp_path / "videos" / "20260803_120000.braid"
    video_folder.mkdir(parents=True)
    # obj_id=1: 25-frame video frame offset lands inside a 100-frame video.
    _write_video_csv(video_folder / "obj_id_1_frame_12345.csv", 100)
    # obj_id=2: no matching video CSV on disk (camera wasn't active / file missing).

    rows = build_alignment(
        braid_dir,
        video_folder,
        camera_fps=500.0,
        braid_fps=100.0,
        systems=("opto", "visual"),
    )
    assert len(rows) == 2

    row1 = next(r for r in rows if r.obj_id == 1)
    assert row1.braid_frame_delta == 5
    assert row1.video_frame == 25
    assert row1.video_frame_count == 100
    assert not row1.out_of_range
    assert row1.sham is False

    row2 = next(r for r in rows if r.obj_id == 2)
    assert row2.video_frame_count is None
    assert not row2.out_of_range  # unknown video length can't be flagged out-of-range
    assert row2.sham is True


def test_build_alignment_filters_by_system(tmp_path):
    lens_row = _opto_row(obj_id=1, frame=12350, record_frame=12345)
    lens_row["system"] = "lens"
    braid_dir = _make_braid_folder(tmp_path, [_opto_row(), lens_row])

    rows = build_alignment(
        braid_dir,
        tmp_path / "videos",
        camera_fps=500.0,
        braid_fps=100.0,
        systems=("opto", "visual"),
    )
    assert len(rows) == 1
    assert rows[0].system == "opto"

    rows_with_lens = build_alignment(
        braid_dir,
        tmp_path / "videos",
        camera_fps=500.0,
        braid_fps=100.0,
        systems=("lens",),
    )
    assert len(rows_with_lens) == 1
    assert rows_with_lens[0].system == "lens"


def test_build_alignment_skips_rows_missing_frame(tmp_path):
    incomplete_row = _opto_row()
    incomplete_row["record_frame"] = ""
    braid_dir = _make_braid_folder(tmp_path, [incomplete_row])

    rows = build_alignment(
        braid_dir,
        tmp_path / "videos",
        camera_fps=500.0,
        braid_fps=100.0,
        systems=("opto", "visual"),
    )
    assert rows == []
