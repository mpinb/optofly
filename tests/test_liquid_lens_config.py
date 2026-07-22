from pathlib import Path

import pytest
import tomli_w
import tomllib

from src.utils.config import LiquidLensConfig


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
        LiquidLensConfig(config_path)


def test_linear_predictor_still_works_without_kalman_only_params(tmp_path):
    config_path = _config_with_predictor(tmp_path, "linear")
    cfg = LiquidLensConfig(config_path)
    assert cfg.predictor == "linear"
    assert hasattr(cfg, "system_latency")
    assert hasattr(cfg, "prediction_horizon")
    assert not hasattr(cfg, "process_noise")
    assert not hasattr(cfg, "measurement_noise")
    assert not hasattr(cfg, "initial_covariance")
    assert not hasattr(cfg, "velocity_noise")


def test_none_predictor_still_works(tmp_path):
    config_path = _config_with_predictor(tmp_path, "none")
    cfg = LiquidLensConfig(config_path)
    assert cfg.predictor == "none"
