import json

import zmq

from src.processes.led import OptoTriggerWorker
from src.utils.config import ZMQConfig


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


def test_initialize_zmq_configures_latency_socket_as_non_blocking():
    """A dead/slow LatencyLogger must never be able to block this process's
    opto-trigger loop. Without SNDTIMEO=0/LINGER=0, a full SNDHWM=1000 queue
    makes the next latency_socket.send() hang forever. Exercises the real
    _initialize_zmq() setup path (not a fake socket) against the checked-in
    example config, so getsockopt reflects genuine zmq behavior."""
    worker = object.__new__(OptoTriggerWorker)
    worker.zmq_config = ZMQConfig("configs/config.example.toml")
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
