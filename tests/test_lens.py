import json

import pytest
import zmq

from src.processes.lens import LiquidLens, _is_lens_rate_limited


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


def test_get_latest_active_update_ignores_other_object_ids(lens):
    """BraidPublisher switches its active-object broadcast to whichever
    object most recently triggered ZONE_ENTER, but LiquidLens ignores
    ZONE_ENTER while a trial is already in progress -- if a second object
    triggers before the first trial ends, the two processes disagree
    about which object is 'active'. Filtering by obj_id here keeps
    current_tracked_obj authoritative regardless of what BraidPublisher
    is currently broadcasting."""
    lens.current_tracked_obj = 7
    lens.active_braid_socket = FakeSocket(
        [
            make_active_message({"obj_id": 8, "frame": 1, "z": 0.9}),
            make_active_message({"obj_id": 7, "frame": 11, "z": 0.1}),
            make_active_message({"obj_id": 8, "frame": 2, "z": 0.9}),
        ]
    )

    assert lens._get_latest_active_update() == {"obj_id": 7, "frame": 11, "z": 0.1}


def test_rate_limited_when_recent_command_and_no_pending_first_update():
    assert (
        _is_lens_rate_limited(
            pending_first_update=None, last_cmd_time=100.0, now_monotonic=100.01
        )
        is True
    )


def test_not_rate_limited_once_25ms_have_elapsed():
    assert (
        _is_lens_rate_limited(
            pending_first_update=None, last_cmd_time=100.0, now_monotonic=100.03
        )
        is False
    )


def test_never_rate_limited_for_pending_first_update_even_if_recent():
    """The first command after ZONE_ENTER must always be evaluated -- the
    late rate-limit check right before the serial write still enforces the
    25ms floor for this path, so it's safe (and necessary, to avoid
    dropping the trial-onset command) to skip the early check here."""
    assert (
        _is_lens_rate_limited(
            pending_first_update={"obj_id": 7},
            last_cmd_time=100.0,
            now_monotonic=100.001,
        )
        is False
    )


def test_not_rate_limited_before_any_command_sent():
    assert (
        _is_lens_rate_limited(
            pending_first_update=None, last_cmd_time=0.0, now_monotonic=5.0
        )
        is False
    )


def test_not_rate_limited_before_any_command_when_elapsed_time_would_otherwise_trigger():
    """Distinguishes the last_cmd_time > 0 guard from the elapsed-time
    check itself -- at now_monotonic=0.01 (10ms), elapsed would be within
    the 25ms window if last_cmd_time were treated as a real prior command
    time of 0.0, so only the explicit `last_cmd_time > 0` guard (not the
    elapsed-time arithmetic) is what makes this return False."""
    assert (
        _is_lens_rate_limited(
            pending_first_update=None, last_cmd_time=0.0, now_monotonic=0.01
        )
        is False
    )


def test_rate_limited_at_exact_25ms_boundary_is_not_rate_limited():
    """25ms elapsed exactly should NOT be rate-limited (the check is a
    strict less-than: `< 0.025`, not `<= 0.025`) -- catches a boundary
    off-by-one between < and <=.

    last_cmd_time=0.001 and now_monotonic=last_cmd_time + 0.025 are chosen
    so that now_monotonic - last_cmd_time floating-point-evaluates to
    exactly 0.025. Round-number choices like last_cmd_time=100.0,
    now_monotonic=100.025 don't work: 100.025 - 100.0 rounds to
    0.025000000000005684 (slightly ABOVE 0.025), which is False under
    both `<` and `<=` and fails to discriminate the boundary bug."""
    last_cmd_time = 0.001
    now_monotonic = last_cmd_time + 0.025
    assert (now_monotonic - last_cmd_time) == 0.025  # sanity: exact fp boundary
    assert (
        _is_lens_rate_limited(
            pending_first_update=None,
            last_cmd_time=last_cmd_time,
            now_monotonic=now_monotonic,
        )
        is False
    )
