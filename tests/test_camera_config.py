import pytest

from src.utils.config import CameraConfig


def _section(**overrides):
    base = {
        "active": True,
        "resolution": [2112, 2112],
        "fps": 500,
        "exposure_time": 1000.0,
        "max_recording_time": 5.0,
        "save_folder": "camera_videos",
    }
    base.update(overrides)
    return base


def test_from_section_flat_fov_box():
    cfg = CameraConfig.from_section(_section())
    assert cfg.active is True
    assert cfg.sensor_width_px == 2112
    assert cfg.sensor_height_px == 2112
    assert cfg.fps == 500.0
    assert cfg.fov_frustum is False
    assert cfg.fov_x_min == -0.1
    assert cfg.fov_x_max == 0.1
    assert cfg.fov_near_z is None


def test_from_section_frustum_fov():
    section = _section(
        FOV={
            "near": {
                "z": 0.10,
                "x_min": -0.03,
                "x_max": 0.03,
                "y_min": -0.03,
                "y_max": 0.03,
            },
            "far": {
                "z": 0.30,
                "x_min": -0.06,
                "x_max": 0.06,
                "y_min": -0.06,
                "y_max": 0.06,
            },
        }
    )
    cfg = CameraConfig.from_section(section)
    assert cfg.fov_frustum is True
    assert cfg.fov_near_z == 0.10
    assert cfg.fov_x_min == -0.06  # flat attrs mirror the far plane
    assert cfg.fov_x_max == 0.06


def test_from_section_missing_resolution_raises():
    section = _section()
    del section["resolution"]
    with pytest.raises(ValueError, match="resolution"):
        CameraConfig.from_section(section)


def test_from_section_missing_fps_raises():
    section = _section()
    del section["fps"]
    with pytest.raises(ValueError, match="fps"):
        CameraConfig.from_section(section)


def test_frozen_instance_cannot_be_mutated():
    cfg = CameraConfig.from_section(_section())
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        cfg.save_folder = "somewhere_else"


def test_path_based_constructor_still_works():
    cfg = CameraConfig.from_path("configs/config.example.toml")
    assert cfg.fps > 0
