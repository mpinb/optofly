import pytest

from src.utils.config import ZMQConfig


def _section(**overrides):
    base = {
        "braid_port": 5555,
        "trigger_port": 5556,
        "active_braid_port": 5557,
        "latency_port": 5558,
        "braid_topic": "BRAID",
    }
    base.update(overrides)
    return base


def test_from_section_builds_expected_fields():
    cfg = ZMQConfig.from_section(_section())
    assert cfg.braid_port == 5555
    assert cfg.zone_enter_topic == "ZONE_ENTER"
    assert cfg.transport == "tcp"


def test_from_section_missing_braid_port_raises():
    section = _section()
    del section["braid_port"]
    with pytest.raises(ValueError, match="braid_port"):
        ZMQConfig.from_section(section)


def test_from_section_duplicate_ports_raises():
    with pytest.raises(ValueError, match="port"):
        ZMQConfig.from_section(_section(trigger_port=5555))


def test_frozen_instance_cannot_be_mutated():
    cfg = ZMQConfig.from_section(_section())
    with pytest.raises(Exception):
        cfg.braid_port = 9999


def test_address_helpers_still_work():
    cfg = ZMQConfig.from_section(_section())
    assert cfg.get_subscriber_address(5555) == "tcp://localhost:5555"
    assert cfg.get_publisher_address(5555) == "tcp://*:5555"


def test_path_based_constructor_still_works():
    cfg = ZMQConfig.from_path("configs/config.example.toml")
    assert cfg.braid_port > 0


def test_from_section_defaults_opto_and_visual_enter_topics():
    cfg = ZMQConfig.from_section(_section())
    assert cfg.opto_enter_topic == "OPTO_ZONE_ENTER"
    assert cfg.visual_enter_topic == "VISUAL_ZONE_ENTER"


def test_from_section_reads_explicit_opto_and_visual_enter_topics():
    cfg = ZMQConfig.from_section(
        _section(opto_enter_topic="CUSTOM_OPTO", visual_enter_topic="CUSTOM_VISUAL")
    )
    assert cfg.opto_enter_topic == "CUSTOM_OPTO"
    assert cfg.visual_enter_topic == "CUSTOM_VISUAL"
