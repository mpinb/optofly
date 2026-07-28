import pytest

from src.utils.config import CameraConfig, TriggerHandlerConfig, ZMQConfig


def _camera():
    return CameraConfig.from_section(
        {"active": True, "resolution": [640, 480], "fps": 100}
    )


def _zmq():
    return ZMQConfig.from_section(
        {"braid_port": 5555, "trigger_port": 5556, "braid_topic": "BRAID"}
    )


def test_from_section_builds_expected_fields():
    camera = _camera()
    zmq = _zmq()
    cfg = TriggerHandlerConfig.from_section(
        {"zone_timeout": 3.0, "cooldown_period": 5.0, "z_min": 0.1, "z_max": 0.3},
        camera=camera,
        zmq=zmq,
    )
    assert cfg.zone_timeout == 3.0
    assert cfg.cooldown_period == 5.0
    assert cfg.fov_x_min == camera.fov_x_min  # pulled from camera, not re-parsed
    assert cfg.zmq is zmq  # same instance, not a re-construction


def test_from_section_z_min_must_be_less_than_z_max():
    with pytest.raises(ValueError, match="z_min must be less than z_max"):
        TriggerHandlerConfig.from_section(
            {"z_min": 0.5, "z_max": 0.1}, camera=_camera(), zmq=_zmq()
        )


def test_frozen_instance_cannot_be_mutated():
    cfg = TriggerHandlerConfig.from_section({}, camera=_camera(), zmq=_zmq())
    with pytest.raises(Exception):
        cfg.zone_timeout = 99.0


def test_path_based_constructor_still_works():
    cfg = TriggerHandlerConfig("configs/config.example.toml")
    assert cfg.zone_timeout > 0
    assert cfg.zmq.braid_port > 0


def test_from_section_defaults_zone_scales_to_expected_values():
    cfg = TriggerHandlerConfig.from_section({}, camera=_camera(), zmq=_zmq())
    assert cfg.opto_zone_scale == 0.5
    assert cfg.visual_zone_scale == 1.0


def test_from_section_reads_explicit_zone_scales():
    cfg = TriggerHandlerConfig.from_section(
        {"opto_zone_scale": 0.3, "visual_zone_scale": 0.8},
        camera=_camera(),
        zmq=_zmq(),
    )
    assert cfg.opto_zone_scale == 0.3
    assert cfg.visual_zone_scale == 0.8


def test_from_section_opto_zone_scale_zero_raises():
    with pytest.raises(ValueError, match="opto_zone_scale must be in"):
        TriggerHandlerConfig.from_section(
            {"opto_zone_scale": 0.0}, camera=_camera(), zmq=_zmq()
        )


def test_from_section_visual_zone_scale_above_one_raises():
    with pytest.raises(ValueError, match="visual_zone_scale must be in"):
        TriggerHandlerConfig.from_section(
            {"visual_zone_scale": 1.5}, camera=_camera(), zmq=_zmq()
        )
