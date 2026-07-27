import pytest

from src.utils.config import BraidPublisherConfig, CameraConfig, TriggerHandlerConfig, ZMQConfig


def _camera():
    return CameraConfig.from_section(
        {"active": True, "resolution": [640, 480], "fps": 100}
    )


def _zmq():
    return ZMQConfig.from_section(
        {"braid_port": 5555, "trigger_port": 5556, "braid_topic": "BRAID"}
    )


def _trigger_handler():
    return TriggerHandlerConfig.from_section({"zone_timeout": 3.0}, camera=_camera(), zmq=_zmq())


def test_from_section_builds_expected_fields():
    cfg = BraidPublisherConfig.from_section(
        {
            "host": "192.168.1.10",
            "events_port": 8397,
            "callback_port": 12345,
            "experiments_path": "/mnt/data/experiments/",
        },
        zmq=_zmq(),
        trigger_handler=_trigger_handler(),
    )
    assert cfg.host == "192.168.1.10"
    assert cfg.callback_port == 12345
    assert cfg.experiments_path == "/mnt/data/experiments/"
    assert cfg.url == "http://192.168.1.10:8397"
    assert cfg.callback_url == "http://192.168.1.10:12345"
    assert cfg.zone_timeout == 3.0  # pulled from trigger_handler, not re-parsed


def test_from_section_defaults():
    cfg = BraidPublisherConfig.from_section({}, zmq=_zmq(), trigger_handler=_trigger_handler())
    assert cfg.host == "127.0.0.1"
    assert cfg.experiments_path == "/mnt/data/experiments/"


def test_from_section_non_positive_timeout_raises():
    with pytest.raises(ValueError, match="timeout must be positive"):
        BraidPublisherConfig.from_section(
            {"timeout": 0}, zmq=_zmq(), trigger_handler=_trigger_handler()
        )


def test_frozen_instance_cannot_be_mutated():
    cfg = BraidPublisherConfig.from_section({}, zmq=_zmq(), trigger_handler=_trigger_handler())
    with pytest.raises(Exception):
        cfg.host = "somewhere-else"
