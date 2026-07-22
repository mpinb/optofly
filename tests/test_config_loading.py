import tomllib

import pytest

from src.utils import config as config_module
from src.utils.config import CameraConfig, LiquidLensConfig, TriggerHandlerConfig


@pytest.fixture(autouse=True)
def clear_toml_cache():
    config_module._TOML_CACHE.clear()
    yield
    config_module._TOML_CACHE.clear()


def test_toml_parsed_once_across_many_config_objects(monkeypatch):
    """LiquidLensConfig construction fans out into TriggerHandlerConfig,
    CameraConfig, and ZMQConfig, each of which used to re-open and
    re-parse the same TOML file independently. Before the fix this measured
    ~3000 tomllib.load calls for one LiquidLensConfig(); it must be 1."""
    call_count = {"n": 0}
    real_load = tomllib.load

    def counting_load(f):
        call_count["n"] += 1
        return real_load(f)

    monkeypatch.setattr(config_module.tomllib, "load", counting_load)

    LiquidLensConfig("configs/config.example.toml")

    assert call_count["n"] == 1


def test_camera_config_does_not_recurse_into_trigger_handler_config():
    """CameraConfig must not construct TriggerHandlerConfig (which itself
    constructs CameraConfig) -- that cycle is what made the call count in
    the test above blow up in the first place. Constructing CameraConfig
    alone must succeed quickly and not raise RecursionError."""
    cfg = CameraConfig("configs/config.example.toml")
    assert cfg.fov_x_min < cfg.fov_x_max


def test_trigger_handler_config_still_gets_fov_from_camera_config():
    """The one-directional CameraConfig -> nothing / TriggerHandlerConfig ->
    CameraConfig relationship must still work after breaking the cycle."""
    trigger_cfg = TriggerHandlerConfig("configs/config.example.toml")
    camera_cfg = CameraConfig("configs/config.example.toml")
    assert trigger_cfg.fov_x_min == camera_cfg.fov_x_min
    assert trigger_cfg.fov_x_max == camera_cfg.fov_x_max
