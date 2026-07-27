import pytest

from src.utils.config import OptoTriggerConfig


def _section(**overrides):
    base = {
        "active": True,
        "port": "/dev/opto_trigger",
        "baudrate": 115200,
        "duration": [100, 200],
        "intensity": [128, 255],
        "frequency": 0,
        "color": "red",
        "sham_probability": 0.1,
    }
    base.update(overrides)
    return base


def test_from_section_builds_expected_fields():
    cfg = OptoTriggerConfig.from_section(_section())
    assert cfg.active is True
    assert cfg.port == "/dev/opto_trigger"
    assert cfg.duration_options == [100, 200]
    assert cfg.intensity_options == [128, 255]
    assert cfg.frequency_options == [0]
    assert cfg.duration == 100
    assert cfg.intensity == 128
    assert cfg.frequency == 0
    assert cfg.color == "red"
    assert cfg.sham_probability == 0.1


def test_from_section_missing_port_raises():
    section = _section()
    del section["port"]
    with pytest.raises(ValueError, match="opto_trigger.port"):
        OptoTriggerConfig.from_section(section)


def test_from_section_invalid_sham_probability_raises():
    with pytest.raises(ValueError, match="sham_probability"):
        OptoTriggerConfig.from_section(_section(sham_probability=1.5))


def test_config_object_stays_mutable_for_set_parameters():
    """OptoTrigger.set_parameters() mutates these fields once per trigger
    to record the balanced-randomization selection -- must keep working."""
    cfg = OptoTriggerConfig.from_section(_section())
    cfg.duration = 200
    cfg.intensity = 255
    cfg.frequency = 10
    cfg.set_color("blue")
    assert cfg.duration == 200
    assert cfg.intensity == 255
    assert cfg.frequency == 10
    assert cfg.color == "blue"


def test_path_based_constructor_still_works():
    cfg = OptoTriggerConfig("configs/config.example.toml")
    assert cfg.port
    assert cfg.color in OptoTriggerConfig.valid_colors()
