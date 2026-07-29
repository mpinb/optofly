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
    cfg = TriggerHandlerConfig.from_path("configs/config.example.toml")
    assert cfg.zone_timeout > 0
    assert cfg.zmq.braid_port > 0
