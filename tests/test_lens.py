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


@pytest.fixture
def lens():
    instance = object.__new__(LiquidLens)
    instance.current_tracked_obj = 7
    instance.braid_socket = FakeSocket()
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


def test_get_next_update_for_current_object_returns_first_match(lens):
    lens.braid_socket = FakeSocket(
        [
            make_braid_message({"Update": {"obj_id": 4, "frame": 1, "x": 0.1}}),
            make_braid_message({"Update": {"obj_id": 7, "frame": 2, "x": 0.2}}),
            make_braid_message({"Update": {"obj_id": 9, "frame": 3, "x": 0.9}}),
        ]
    )

    update, saw_death = lens._get_next_update_for_current_object()

    assert update == {"obj_id": 7, "frame": 2, "x": 0.2}
    assert saw_death is False
    assert lens.braid_socket.messages == [
        make_braid_message({"Update": {"obj_id": 9, "frame": 3, "x": 0.9}})
    ]


def test_get_next_update_for_current_object_uses_newer_match_within_scan_ahead(lens):
    lens.braid_socket = FakeSocket(
        [
            make_braid_message({"Update": {"obj_id": 4, "frame": 1, "x": 0.1}}),
            make_braid_message({"Update": {"obj_id": 7, "frame": 2, "x": 0.2}}),
            make_braid_message({"Death": 7}),
            make_braid_message(
                {"Update": {"obj_id": 7, "frame": 3, "x": 0.3}, "t_relay": 123.0}
            ),
            make_braid_message({"Update": {"obj_id": 1, "frame": 4, "x": 0.4}}),
        ]
    )

    update, saw_death = lens._get_next_update_for_current_object(scan_ahead=2)

    assert update == {"obj_id": 7, "frame": 3, "x": 0.3}
    assert saw_death is True
    assert lens.braid_socket.messages == [
        make_braid_message({"Update": {"obj_id": 1, "frame": 4, "x": 0.4}})
    ]


def test_get_next_update_for_current_object_stops_after_scan_budget(lens):
    lens.braid_socket = FakeSocket(
        [
            make_braid_message({"Update": {"obj_id": 7, "frame": 2, "x": 0.2}}),
            make_braid_message({"Update": {"obj_id": 1, "frame": 3, "x": 0.3}}),
            make_braid_message({"Update": {"obj_id": 2, "frame": 4, "x": 0.4}}),
            make_braid_message({"Update": {"obj_id": 7, "frame": 5, "x": 0.5}}),
        ]
    )

    update, saw_death = lens._get_next_update_for_current_object(scan_ahead=2)

    assert update == {"obj_id": 7, "frame": 2, "x": 0.2}
    assert saw_death is False
    assert lens.braid_socket.messages == [
        make_braid_message({"Update": {"obj_id": 7, "frame": 5, "x": 0.5}})
    ]
