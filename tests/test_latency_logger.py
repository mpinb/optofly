import json

import pytest
import zmq

from src.processes.latency_logger import LatencyLogger


class FakeSocket:
    def __init__(self, messages=None):
        self.messages = list(messages or [])

    def recv(self, flags=0):
        if not self.messages:
            raise zmq.Again()
        return self.messages.pop(0)


class FakeCSVWriter:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def close(self):
        pass


def _make_logger():
    logger = object.__new__(LatencyLogger)
    logger.csv_writer = FakeCSVWriter()
    logger.logger = type(
        "Logger",
        (),
        {
            "error": lambda *a, **k: None,
            "debug": lambda *a, **k: None,
            "info": lambda *a, **k: None,
        },
    )()
    return logger


def _msg(**overrides):
    base = {
        "system": "opto",
        "obj_id": 7,
        "frame": 100,
        "braid_timestamp": 1000.0,
        "trigger_timestamp": 1000.003,
        "activation_timestamp": 1000.011,
        "sham": False,
    }
    base.update(overrides)
    return base


def test_computes_latency_ms_for_real_activation():
    logger = _make_logger()

    logger._handle_message(json.dumps(_msg()).encode("utf-8"))

    assert len(logger.csv_writer.rows) == 1
    row = logger.csv_writer.rows[0]
    assert row["latency_ms"] == pytest.approx(11.0)
    assert row["sham"] is False


def test_sham_row_gets_blank_activation_and_latency():
    logger = _make_logger()

    logger._handle_message(
        json.dumps(_msg(sham=True, activation_timestamp=None)).encode("utf-8")
    )

    row = logger.csv_writer.rows[0]
    assert row["activation_timestamp"] is None
    assert row["latency_ms"] is None


def test_missing_activation_timestamp_on_non_sham_row_leaves_latency_blank():
    logger = _make_logger()

    logger._handle_message(
        json.dumps(_msg(activation_timestamp=None)).encode("utf-8")
    )

    row = logger.csv_writer.rows[0]
    assert row["latency_ms"] is None


def test_malformed_message_missing_required_key_is_skipped_not_crashed():
    logger = _make_logger()
    incomplete = _msg()
    del incomplete["braid_timestamp"]

    logger._handle_message(json.dumps(incomplete).encode("utf-8"))  # must not raise

    assert logger.csv_writer.rows == []


def test_invalid_json_is_skipped_not_crashed():
    logger = _make_logger()

    logger._handle_message(b"not json")  # must not raise

    assert logger.csv_writer.rows == []


def test_csv_write_failure_is_logged_not_raised():
    logger = _make_logger()

    class RaisingWriter:
        def append(self, row):
            raise RuntimeError("disk full")

    logger.csv_writer = RaisingWriter()

    logger._handle_message(json.dumps(_msg()).encode("utf-8"))  # must not raise
