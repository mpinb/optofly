"""Tests for calibrate_frustum_fov — config writer and FOV computation.

No hardware required: camera, lens, and BRAID tracker are not exercised.
"""

import re
import textwrap
from pathlib import Path

import pytest

from src.tools.calibrate_frustum_fov import _compute_fov, _write_frustum_to_config


# ---------------------------------------------------------------------------
# _compute_fov
# ---------------------------------------------------------------------------


def test_compute_fov_basic():
    pts = [(-0.02, -0.01), (0.03, -0.01), (0.03, 0.04), (-0.02, 0.04)]
    fov = _compute_fov(pts)
    assert fov["x_min"] == pytest.approx(-0.02)
    assert fov["x_max"] == pytest.approx(0.03)
    assert fov["y_min"] == pytest.approx(-0.01)
    assert fov["y_max"] == pytest.approx(0.04)


def test_compute_fov_single_point():
    fov = _compute_fov([(0.01, 0.02)])
    assert fov["x_min"] == fov["x_max"] == pytest.approx(0.01)
    assert fov["y_min"] == fov["y_max"] == pytest.approx(0.02)


def test_compute_fov_many_points():
    import random

    random.seed(42)
    pts = [
        (random.uniform(-0.05, 0.05), random.uniform(-0.05, 0.05)) for _ in range(20)
    ]
    fov = _compute_fov(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert fov["x_min"] == pytest.approx(min(xs))
    assert fov["x_max"] == pytest.approx(max(xs))
    assert fov["y_min"] == pytest.approx(min(ys))
    assert fov["y_max"] == pytest.approx(max(ys))


# ---------------------------------------------------------------------------
# _write_frustum_to_config
# ---------------------------------------------------------------------------


_FLAT_CONFIG = textwrap.dedent("""\
    [braid_publisher]
    host = "127.0.0.1"

    [camera]
    fps = 500

    [camera.FOV]
    # some comment
    x_min = -0.0218
    x_max = 0.039
    y_min = -0.025
    y_max = 0.041
    # optional calibration file
    # braid_ximea_calibration_file = "calibrations/braid_to_ximea.npz"

    [opto_trigger]
    active = false
""")

_NEAR_FOV = {"x_min": -0.015, "x_max": 0.025, "y_min": -0.018, "y_max": 0.030}
_FAR_FOV = {"x_min": -0.022, "x_max": 0.040, "y_min": -0.026, "y_max": 0.042}


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(_FLAT_CONFIG)
    return p


def test_write_frustum_creates_near_far(config_file: Path):
    _write_frustum_to_config(str(config_file), 0.10, _NEAR_FOV, 0.25, _FAR_FOV)
    result = config_file.read_text()
    assert "[camera.FOV.near]" in result
    assert "[camera.FOV.far]" in result


def test_write_frustum_removes_flat_keys(config_file: Path):
    _write_frustum_to_config(str(config_file), 0.10, _NEAR_FOV, 0.25, _FAR_FOV)
    result = config_file.read_text()
    # Flat keys should no longer appear as assignments outside of a sub-table context
    # (the new block replaces everything from [camera.FOV] to [opto_trigger])
    between = result.split("[camera.FOV]")[1].split("[opto_trigger]")[0]
    assert not re.search(r"^x_min\s*=\s*-0\.0218", between, re.MULTILINE)
    assert not re.search(r"^y_max\s*=\s*0\.041", between, re.MULTILINE)


def test_write_frustum_correct_values(config_file: Path):
    _write_frustum_to_config(str(config_file), 0.10, _NEAR_FOV, 0.25, _FAR_FOV)
    result = config_file.read_text()
    assert "z     = 0.1000" in result
    assert "z     = 0.2500" in result
    assert "x_min = -0.01500" in result
    assert "x_max = 0.04000" in result


def test_write_frustum_preserves_surrounding_sections(config_file: Path):
    _write_frustum_to_config(str(config_file), 0.10, _NEAR_FOV, 0.25, _FAR_FOV)
    result = config_file.read_text()
    assert "[braid_publisher]" in result
    assert "[opto_trigger]" in result
    assert "active = false" in result


def test_write_frustum_idempotent(config_file: Path):
    # Writing twice should produce identical output
    _write_frustum_to_config(str(config_file), 0.10, _NEAR_FOV, 0.25, _FAR_FOV)
    first = config_file.read_text()
    _write_frustum_to_config(str(config_file), 0.10, _NEAR_FOV, 0.25, _FAR_FOV)
    second = config_file.read_text()
    assert first == second


def test_write_frustum_missing_section_raises(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[some_other_section]\nfoo = 1\n")
    with pytest.raises(RuntimeError, match=r"\[camera\.FOV\]"):
        _write_frustum_to_config(str(cfg), 0.10, _NEAR_FOV, 0.25, _FAR_FOV)
