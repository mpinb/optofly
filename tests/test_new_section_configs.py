import logging

import pytest

from src.utils.config import LoggingConfig, VisualStimuliConfig


def test_logging_config_defaults_and_level_int():
    cfg = LoggingConfig.from_section({})
    assert cfg.level == "INFO"
    assert cfg.level_int() == logging.INFO


def test_logging_config_explicit_level():
    cfg = LoggingConfig.from_section({"level": "debug"})
    assert cfg.level == "DEBUG"
    assert cfg.level_int() == logging.DEBUG


def test_logging_config_invalid_level_falls_back_to_info():
    cfg = LoggingConfig.from_section({"level": "NOT_A_REAL_LEVEL"})
    assert cfg.level_int() == logging.INFO


def test_visual_stimuli_config_defaults():
    cfg = VisualStimuliConfig.from_section({})
    assert cfg.active is False
    assert cfg.config_file == "configs/visual_stimuli.toml"


def test_frozen_instances_cannot_be_mutated():
    cfg = VisualStimuliConfig.from_section({})
    with pytest.raises(Exception):
        cfg.active = True
