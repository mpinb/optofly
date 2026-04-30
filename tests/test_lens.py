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


def make_braid_message(payload):
    return [b"BRAID", json.dumps(payload).encode("utf-8")]


def make_active_message(payload):
    return [b"ACTIVE_BRAID", json.dumps(payload).encode("utf-8")]


@pytest.fixture
def lens():
    instance = object.__new__(LiquidLens)
    instance.current_tracked_obj = 7
    instance.braid_socket = FakeSocket()
    instance.active_braid_socket = FakeSocket()
    return instance


def test_drain_braid_idle_discards_all_pending_messages(lens):
    lens.braid_socket = FakeSocket(
        [
            make_braid_message({"Update": {"obj_id": 1, "frame": 10}}),
            make_braid_message({"Update": {"obj_id": 7, "frame": 11}}),
            make_braid_message({"Death": 7}),
        ]
    )

    lens._drain_braid_idle()

    assert lens.braid_socket.messages == []


def test_get_next_update_skips_to_latest_match(lens):
    lens.braid_socket = FakeSocket(
        [
            make_braid_message({"Update": {"obj_id": 4, "frame": 1, "x": 0.1}}),
            make_braid_message({"Update": {"obj_id": 7, "frame": 2, "x": 0.2}}),
            make_braid_message({"Update": {"obj_id": 9, "frame": 3, "x": 0.9}}),
            make_braid_message(
                {"Update": {"obj_id": 7, "frame": 5, "x": 0.5, "t_relay": 123.0}}
            ),
        ]
    )

    update, saw_death = lens._get_next_update_for_current_object()

    assert update == {"obj_id": 7, "frame": 5, "x": 0.5, "t_relay": 123.0}
    assert saw_death is False
    assert lens.braid_socket.messages == []


def test_get_next_update_reports_death_during_drain(lens):
    lens.braid_socket = FakeSocket(
        [
            make_braid_message({"Update": {"obj_id": 7, "frame": 2, "x": 0.2}}),
            make_braid_message({"Update": {"obj_id": 7, "frame": 3, "x": 0.3}}),
            make_braid_message({"Death": 7}),
            make_braid_message({"Update": {"obj_id": 1, "frame": 4, "x": 0.4}}),
        ]
    )

    update, saw_death = lens._get_next_update_for_current_object()

    assert update == {"obj_id": 7, "frame": 3, "x": 0.3}
    assert saw_death is True
    assert lens.braid_socket.messages == []


def test_get_next_update_returns_none_when_no_match(lens):
    lens.braid_socket = FakeSocket(
        [
            make_braid_message({"Update": {"obj_id": 4, "frame": 1}}),
            make_braid_message({"Update": {"obj_id": 9, "frame": 2}}),
            make_braid_message({"Death": 4}),
        ]
    )

    update, saw_death = lens._get_next_update_for_current_object()

    assert update is None
    assert saw_death is False
    assert lens.braid_socket.messages == []


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
    lens.last_position_time = None
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
