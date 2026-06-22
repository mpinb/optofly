import json

import pytest
import zmq

from src.processes.lens import LiquidLens


class FakeSocket:
    def __init__(self, messages=None):
        self.messages = list(messages or [])

    def recv_multipart(self, flags=0):
        if not self.messages:
            raise zmq.Again()
        return self.messages.pop(0)


def make_active_message(payload):
    return [b"ACTIVE_BRAID", json.dumps(payload).encode("utf-8")]


@pytest.fixture
def lens():
    instance = object.__new__(LiquidLens)
    instance.current_tracked_obj = 7
    instance.active_braid_socket = FakeSocket()
    return instance


def test_drain_active_braid_idle_discards_all_pending_messages(lens):
    lens.active_braid_socket = FakeSocket(
        [
            make_active_message({"obj_id": 7, "frame": 11}),
            make_active_message({"obj_id": 7, "frame": 12}),
        ]
    )

    lens._drain_active_braid_idle()

    assert lens.active_braid_socket.messages == []


def test_get_latest_active_update_returns_newest_payload(lens):
    lens.active_braid_socket = FakeSocket(
        [
            make_active_message({"obj_id": 7, "frame": 11, "z": 0.1}),
            make_active_message({"obj_id": 7, "frame": 12, "z": 0.2}),
        ]
    )

    assert lens._get_latest_active_update() == {"obj_id": 7, "frame": 12, "z": 0.2}
    assert lens.active_braid_socket.messages == []


def test_zone_enter_payload_becomes_pending_first_update(lens):
    lens.zmq_config = type("Config", (), {"zone_enter_topic": "ZONE_ENTER"})()
    lens.is_tracking = False
    lens.current_tracked_obj = None
    lens._timing_rows = ["stale"]
    lens.kalman = "stale"
    lens._recording_obj_id = None
    lens._recording_frame = None
    lens._log_csv = lambda *args, **kwargs: None
    lens.logger = type("Logger", (), {"info": lambda *args, **kwargs: None})()
    lens.trigger_socket = FakeSocket(
        [
            [
                b"ZONE_ENTER",
                json.dumps({"obj_id": 7, "frame": 12, "x": 0.1, "y": 0.2, "z": 0.3}).encode(
                    "utf-8"
                ),
            ]
        ]
    )

    lens._drain_trigger_socket()

    assert lens.is_tracking is True
    assert lens.current_tracked_obj == 7
    assert lens._pending_first_update == {
        "obj_id": 7,
        "frame": 12,
        "x": 0.1,
        "y": 0.2,
        "z": 0.3,
    }
