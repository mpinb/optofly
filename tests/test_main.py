from main import (
    check_critical_processes_alive,
    check_latency_logger_alive,
    check_recording_time_sufficient,
    handle_metadata_cancellation,
)
from src.utils.config import AppConfig


def _app_config(**toml_overrides):
    import tomli_w
    import tomllib
    from pathlib import Path

    with open("configs/config.example.toml", "rb") as f:
        data = tomllib.load(f)
    for section, values in toml_overrides.items():
        data.setdefault(section, {}).update(values)
    out = Path("/tmp") / "test_main_app_config.toml"
    out.write_bytes(tomli_w.dumps(data).encode("utf-8"))
    return AppConfig.load(str(out))


class _FakeAliveProcess:
    def is_alive(self):
        return True


class _FakeDeadProcess:
    def is_alive(self):
        return False


def test_dead_braid_publisher_produces_its_own_message_not_hardware():
    """A BraidPublisher init failure must be diagnosed as a Braid
    connectivity issue, not misattributed to lens/opto hardware."""
    processes = [
        ("BraidPublisher", _FakeDeadProcess()),
        ("TriggerHandler", _FakeAliveProcess()),
    ]

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "BraidPublisher" in messages[0]
    assert "Braid" in messages[0]
    assert "hardware" not in messages[0].lower()


def test_dead_liquid_lens_produces_hardware_message():
    processes = [("LiquidLens", _FakeDeadProcess())]

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "LiquidLens" in messages[0]
    assert "hardware" in messages[0].lower()


def test_dead_opto_trigger_worker_produces_hardware_message():
    processes = [("OptoTriggerWorker", _FakeDeadProcess())]

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "OptoTriggerWorker" in messages[0]
    assert "hardware" in messages[0].lower()


def test_all_alive_produces_no_messages():
    processes = [
        ("BraidPublisher", _FakeAliveProcess()),
        ("LiquidLens", _FakeAliveProcess()),
        ("OptoTriggerWorker", _FakeAliveProcess()),
    ]

    assert check_critical_processes_alive(processes) == []


def test_non_critical_process_death_is_ignored():
    """A dead Monitoring Server/VisualProcess/CameraProcess must not abort
    the whole experiment — only processes known to fail fast and
    unrecoverably during their own init are critical."""
    processes = [
        ("Monitoring Server", _FakeDeadProcess()),
        ("VisualProcess", _FakeDeadProcess()),
        ("CameraProcess", _FakeDeadProcess()),
    ]

    assert check_critical_processes_alive(processes) == []


def test_dead_trigger_handler_produces_its_own_message():
    """TriggerHandler binds its own ZMQ publisher socket during init and
    exits unrecoverably if the port is already taken -- the same fail-fast
    profile as the other critical processes, so its death during init must
    also be treated as fatal rather than silently running a 24-hour
    experiment with no tracking, no triggers, no recordings."""
    processes = [("TriggerHandler", _FakeDeadProcess())]

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "TriggerHandler" in messages[0]


def test_multiple_dead_critical_processes_each_produce_a_message():
    processes = [
        ("BraidPublisher", _FakeDeadProcess()),
        ("LiquidLens", _FakeDeadProcess()),
    ]

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 2
    joined = " ".join(messages)
    assert "BraidPublisher" in joined
    assert "LiquidLens" in joined


def test_warns_when_max_recording_time_less_than_zone_timeout():
    app_config = _app_config(
        camera={"active": True, "max_recording_time": 1.0},
        trigger_handler={"zone_timeout": 3.0},
    )
    warning = check_recording_time_sufficient(app_config)
    assert warning is not None
    assert "1.0" in warning
    assert "3.0" in warning


def test_no_warning_when_camera_inactive():
    app_config = _app_config(
        camera={"active": False, "max_recording_time": 1.0},
        trigger_handler={"zone_timeout": 3.0},
    )
    assert check_recording_time_sufficient(app_config) is None


def test_no_warning_when_recording_time_sufficient():
    app_config = _app_config(
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


def test_check_latency_logger_alive_returns_none_when_alive():
    assert check_latency_logger_alive(_FakeAliveProcess()) is None


def test_check_latency_logger_alive_returns_warning_when_dead():
    warning = check_latency_logger_alive(_FakeDeadProcess())
    assert warning is not None
    assert "LatencyLogger" in warning


def test_summary_prints_lens_predictor(capsys):
    from main import print_experiment_config

    app_config = _app_config(liquid_lens={"mode": "diopter", "predictor": "linear"})
    print_experiment_config(app_config, ["LiquidLens"])
    out = capsys.readouterr().out
    assert "Predictor: linear" in out
    # The old code printed nothing about the predictor unless the
    # nonexistent kalman.enabled/prediction.enabled keys were set.
    assert "Kalman filter (predictive focus)" not in out
