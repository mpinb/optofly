import tomllib

import pytest

from src.utils import config as config_module
from src.utils.config import CameraConfig, LiquidLensConfig, TriggerHandlerConfig, ZMQConfig


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


def test_zmq_config_has_latency_port_with_default():
    cfg = ZMQConfig("configs/config.example.toml")
    assert cfg.latency_port == 5558


def test_zmq_config_rejects_latency_port_colliding_with_another_port(tmp_path):
    import tomli_w
    import tomllib

    with open("configs/config.example.toml", "rb") as f:
        data = tomllib.load(f)
    data["zmq"]["latency_port"] = data["zmq"]["trigger_port"]
    out = tmp_path / "config.toml"
    out.write_bytes(tomli_w.dumps(data).encode("utf-8"))

    with pytest.raises(ValueError, match="port"):
        ZMQConfig(str(out))
