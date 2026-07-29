import json

import pytest
import zmq

from src.processes.led import OptoTriggerWorker
from src.utils.config import AppConfig, ZMQConfig


class FakeLatencySocket:
    def __init__(self):
        self.sent = []

    def send(self, raw):
        self.sent.append(json.loads(raw))


class FakeCSVWriter:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)


class FakeOptoTrigger:
    def __init__(self, trigger_return):
        self._trigger_return = trigger_return
        self.config = type(
            "Config",
            (),
            {"duration": 100, "intensity": 255, "frequency": 0, "color": "white"},
        )()

    def trigger(self, sham=None):
        return self._trigger_return


def _make_worker(trigger_return):
    worker = object.__new__(OptoTriggerWorker)
    worker.opto_trigger = FakeOptoTrigger(trigger_return)
    worker.csv_writer = FakeCSVWriter()
    worker.latency_socket = FakeLatencySocket()
    worker.logger = type(
        "Logger",
        (),
        {
            "info": lambda *a, **k: None,
            "debug": lambda *a, **k: None,
            "warning": lambda *a, **k: None,
            "error": lambda *a, **k: None,
        },
    )()
    return worker


def test_handle_trigger_uses_extract_trigger_timing_not_raw_fallback_chain():
    worker = _make_worker(trigger_return=(True, False, 500.02))

    worker._handle_trigger(
        {
            "obj_id": 7,
            "frame": 100,
            "braid_timestamp": 500.0,
            "handler_timestamp": 500.01,
            "mean_heading": 0.1,
        }
    )

    row = worker.csv_writer.rows[0]
    assert row["braid_timestamp"] == 500.0
    assert row["trigger_timestamp"] == 500.01


def test_handle_trigger_publishes_latency_for_real_activation():
    worker = _make_worker(trigger_return=(True, False, 500.02))

    worker._handle_trigger(
        {
            "obj_id": 7,
            "frame": 100,
            "braid_timestamp": 500.0,
            "handler_timestamp": 500.01,
            "mean_heading": 0.1,
        }
    )

    sent = worker.latency_socket.sent
    assert len(sent) == 1
    assert sent[0] == {
        "system": "opto",
        "obj_id": 7,
        "frame": 100,
        "braid_timestamp": 500.0,
        "trigger_timestamp": 500.01,
        "activation_timestamp": 500.02,
        "sham": False,
    }


def test_handle_trigger_publishes_latency_with_none_activation_for_sham():
    worker = _make_worker(trigger_return=(True, True, None))

    worker._handle_trigger(
        {
            "obj_id": 7,
            "frame": 100,
            "braid_timestamp": 500.0,
            "handler_timestamp": 500.01,
            "mean_heading": 0.1,
        }
    )

    sent = worker.latency_socket.sent
    assert sent[0]["sham"] is True
    assert sent[0]["activation_timestamp"] is None


class RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args):
        self.warnings.append(msg % args if args else msg)

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class UnopenableOptoTrigger:
    """Stands in for hardware whose serial port cannot be opened."""

    def __init__(self, *args, **kwargs):
        pass

    def initialize(self):
        return False

    def set_backlight(self, intensity):
        raise AssertionError("set_backlight must not run after a failed init")


def _worker_for_initialize(monkeypatch, *, active):
    monkeypatch.setattr("src.processes.led.OptoTrigger", UnopenableOptoTrigger)
    worker = object.__new__(OptoTriggerWorker)
    worker.config_path = "configs/config.example.toml"
    worker.process_name = "OptoTriggerWorker"
    worker.log_level = "INFO"
    worker.log_color = "RED"
    worker.opto_config = AppConfig.load("configs/config.example.toml").opto_trigger
    worker.is_enabled = active
    worker.opto_trigger = None
    worker.logger = RecordingLogger()
    return worker


def test_unopenable_hardware_is_survivable_when_stimulation_is_disabled(monkeypatch):
    """With opto_trigger.active = false the user has asked for no stimulation,
    so a missing Arduino must not take the whole experiment down -- the
    process stays up and only loses the backlight."""
    worker = _worker_for_initialize(monkeypatch, active=False)

    worker.initialize()  # must not raise

    assert worker.opto_trigger is None
    assert any(
        "opto_trigger.active = false" in w for w in worker.logger.warnings
    ), f"expected a warning naming the inactive flag, got {worker.logger.warnings}"
    assert any("/dev/opto_trigger" in w for w in worker.logger.warnings)


def test_unopenable_hardware_still_raises_when_stimulation_is_enabled(monkeypatch):
    """With active = true the user asked for stimulation we cannot deliver,
    so this must stay fatal."""
    worker = _worker_for_initialize(monkeypatch, active=True)

    with pytest.raises(RuntimeError, match="/dev/opto_trigger"):
        worker.initialize()


def test_initialize_zmq_configures_latency_socket_as_non_blocking():
    """A dead/slow LatencyLogger must never be able to block this process's
    opto-trigger loop. Without SNDTIMEO=0/LINGER=0, a full SNDHWM=1000 queue
    makes the next latency_socket.send() hang forever. Exercises the real
    _initialize_zmq() setup path (not a fake socket) against the checked-in
    example config, so getsockopt reflects genuine zmq behavior."""
    worker = object.__new__(OptoTriggerWorker)
    worker.zmq_config = ZMQConfig.from_path("configs/config.example.toml")
    worker.logger = type(
        "Logger",
        (),
        {"debug": lambda *a, **k: None, "error": lambda *a, **k: None},
    )()

    worker._initialize_zmq()

    try:
        assert worker.latency_socket.getsockopt(zmq.SNDTIMEO) == 0
        assert worker.latency_socket.getsockopt(zmq.LINGER) == 0
    finally:
        worker.latency_socket.close()
        worker.trigger_socket.close()
        worker.context.term()
