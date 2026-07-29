from main import (
    check_recording_time_sufficient,
    handle_metadata_cancellation,
)
from src.utils.config import AppConfig


def _app_config(tmp_path, **toml_overrides):
    import tomli_w
    import tomllib

    with open("configs/config.example.toml", "rb") as f:
        data = tomllib.load(f)
    for section, values in toml_overrides.items():
        data.setdefault(section, {}).update(values)
    out = tmp_path / "test_main_app_config.toml"
    out.write_bytes(tomli_w.dumps(data).encode("utf-8"))
    return AppConfig.load(str(out))


def test_warns_when_max_recording_time_less_than_zone_timeout(tmp_path):
    app_config = _app_config(
        tmp_path,
        camera={"active": True, "max_recording_time": 1.0},
        trigger_handler={"zone_timeout": 3.0},
    )
    warning = check_recording_time_sufficient(app_config)
    assert warning is not None
    assert "1.0" in warning
    assert "3.0" in warning


def test_no_warning_when_camera_inactive(tmp_path):
    app_config = _app_config(
        tmp_path,
        camera={"active": False, "max_recording_time": 1.0},
        trigger_handler={"zone_timeout": 3.0},
    )
    assert check_recording_time_sufficient(app_config) is None


def test_no_warning_when_recording_time_sufficient(tmp_path):
    app_config = _app_config(
        tmp_path,
        camera={"active": True, "max_recording_time": 5.0},
        trigger_handler={"zone_timeout": 3.0},
    )
    assert check_recording_time_sufficient(app_config) is None


class _FakeBraidProxy:
    def __init__(self, raise_on_stop=False):
        self.stopped = False
        self.raise_on_stop = raise_on_stop

    def stop_csv_recording(self):
        if self.raise_on_stop:
            raise RuntimeError("connection lost")
        self.stopped = True


def test_handle_metadata_cancellation_stops_recording_when_proxy_present(capsys):
    proxy = _FakeBraidProxy()
    handle_metadata_cancellation(proxy)
    assert proxy.stopped is True
    out = capsys.readouterr().out
    assert "cancelled" in out.lower()
    assert "stopped" in out.lower()


def test_handle_metadata_cancellation_no_stop_call_when_proxy_is_none(capsys):
    """braid_proxy is None whenever the folder already existed (recording
    was not auto-started by this run) -- must not try to stop anything."""
    handle_metadata_cancellation(None)  # must not raise
    out = capsys.readouterr().out
    assert "cancelled" in out.lower()


def test_handle_metadata_cancellation_stop_failure_is_reported_not_raised(capsys):
    proxy = _FakeBraidProxy(raise_on_stop=True)
    handle_metadata_cancellation(proxy)  # must not raise
    out = capsys.readouterr().out
    assert "warning" in out.lower()


def test_load_config_still_returns_app_config():
    from src.utils.config import AppConfig
    from main import load_config

    app_config = load_config("configs/config.example.toml")
    assert isinstance(app_config, AppConfig)


def test_summary_prints_lens_predictor(capsys, tmp_path):
    from main import print_experiment_config

    app_config = _app_config(
        tmp_path, liquid_lens={"mode": "diopter", "predictor": "linear"}
    )
    print_experiment_config(app_config, ["LiquidLens"])
    out = capsys.readouterr().out
    assert "Predictor: linear" in out
    # The old code printed nothing about the predictor unless the
    # nonexistent kalman.enabled/prediction.enabled keys were set.
    assert "Kalman filter (predictive focus)" not in out


def test_dead_process_summary_names_the_process_and_the_reason():
    """"A critical process died during the run" named nothing, even though
    Experiment.status() already carries the reason. On a 24-hour run the
    operator comes back to a terminal holding only that sentence."""
    from main import format_critical_failures

    status = {
        "processes": {
            "BraidPublisher": {"alive": True, "failed_reason": None, "shutdown": None},
            "LiquidLens": {
                "alive": False,
                "failed_reason": (
                    "LiquidLens process exited during the run. RuntimeError: "
                    "field named dpt not found in calibrations/liquid_lens.csv"
                ),
                "shutdown": None,
            },
        }
    }

    lines = format_critical_failures(status)

    assert len(lines) == 1
    assert "LiquidLens" in lines[0]
    assert "liquid_lens.csv" in lines[0]


def test_dead_process_summary_is_empty_when_nothing_failed():
    from main import format_critical_failures

    status = {
        "processes": {
            "BraidPublisher": {"alive": True, "failed_reason": None, "shutdown": None}
        }
    }

    assert format_critical_failures(status) == []


def test_dead_process_summary_reports_every_failure():
    from main import format_critical_failures

    status = {
        "processes": {
            "BraidPublisher": {"alive": False, "failed_reason": "braid is unreachable"},
            "TriggerHandler": {"alive": False, "failed_reason": "trigger_port in use"},
        }
    }

    lines = format_critical_failures(status)

    assert len(lines) == 2
    assert any("braid is unreachable" in line for line in lines)
    assert any("trigger_port in use" in line for line in lines)
