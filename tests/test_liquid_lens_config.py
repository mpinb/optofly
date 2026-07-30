from pathlib import Path

import pytest
import tomli_w
import tomllib

from src.utils.config import CameraConfig, LiquidLensConfig, TriggerHandlerConfig, ZMQConfig


def _config_with_predictor(tmp_path: Path, predictor: str) -> str:
    with open("configs/config.example.toml", "rb") as f:
        data = tomllib.load(f)
    data["liquid_lens"]["predictor"] = predictor
    out = tmp_path / "config.toml"
    out.write_bytes(tomli_w.dumps(data).encode("utf-8"))
    return str(out)


def test_kalman_predictor_value_is_rejected(tmp_path):
    config_path = _config_with_predictor(tmp_path, "kalman")
    with pytest.raises(ValueError, match="predictor"):
        LiquidLensConfig.from_path(config_path)


def test_linear_predictor_still_works_without_kalman_only_params(tmp_path):
    config_path = _config_with_predictor(tmp_path, "linear")
    cfg = LiquidLensConfig.from_path(config_path)
    assert cfg.predictor == "linear"
    assert hasattr(cfg, "system_latency")
    assert hasattr(cfg, "prediction_horizon")
    assert not hasattr(cfg, "process_noise")
    assert not hasattr(cfg, "measurement_noise")
    assert not hasattr(cfg, "initial_covariance")
    assert not hasattr(cfg, "velocity_noise")


def test_none_predictor_still_works(tmp_path):
    config_path = _config_with_predictor(tmp_path, "none")
    cfg = LiquidLensConfig.from_path(config_path)
    assert cfg.predictor == "none"


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
    cfg = LiquidLensConfig.from_section(
        {"port": "/dev/optotune_ld", "predictor": "linear"},
        trigger_handler=_trigger_handler(),
        camera=_camera(),
        zmq=_zmq(),
    )
    assert cfg.port == "/dev/optotune_ld"
    assert cfg.predictor == "linear"
    assert cfg.zone_timeout == 3.0  # pulled from trigger_handler, not re-parsed
    assert cfg.fov_x_min == _camera().fov_x_min


def test_from_section_missing_port_raises():
    with pytest.raises(ValueError, match="liquid_lens.port"):
        LiquidLensConfig.from_section(
            {}, trigger_handler=_trigger_handler(), camera=_camera(), zmq=_zmq()
        )


def test_frozen_instance_cannot_be_mutated():
    cfg = LiquidLensConfig.from_section(
        {"port": "/dev/optotune_ld"},
        trigger_handler=_trigger_handler(),
        camera=_camera(),
        zmq=_zmq(),
    )
    with pytest.raises(Exception):
        cfg.predictor = "none"
