import json
import math

from src.visual.process import VisualProcess, braid_to_world_heading


def test_no_offset_no_flip():
    result = braid_to_world_heading(math.pi / 2, offset_rad=0.0, flip=False)
    assert abs(result - 90.0) < 1e-6


def test_with_offset():
    # offset=pi/2 means "Braid 90 deg corresponds to North"
    result = braid_to_world_heading(math.pi / 2, offset_rad=math.pi / 2, flip=False)
    assert abs(result - 0.0) < 1e-6


def test_with_flip():
    result = braid_to_world_heading(math.pi / 2, offset_rad=0.0, flip=True)
    assert abs(result - (-90.0)) < 1e-6


def test_offset_and_flip():
    result = braid_to_world_heading(math.pi / 2, offset_rad=math.pi / 4, flip=True)
    expected = -math.degrees(math.pi / 2 - math.pi / 4)
    assert abs(result - expected) < 1e-6


class _FakeZmqSocket:
    def __init__(self, messages):
        self._messages = list(messages)

    def recv_multipart(self, flags=0):
        import zmq

        if not self._messages:
            raise zmq.Again()
        return self._messages.pop(0)


def _make_process(zone_enter_topic):
    proc = object.__new__(VisualProcess)
    proc.stop_event = type("Event", (), {"is_set": lambda self: False})()
    proc._zone_enter_topic = zone_enter_topic
    proc._zmq_socket = None
    proc.logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "exception": lambda *a, **k: None},
    )()
    return proc


def test_zmq_poll_task_uses_configured_zone_enter_topic_not_literal():
    """A renamed zmq.zone_enter_topic in config must still be recognized --
    previously _zmq_poll_task compared against the hardcoded literal
    "ZONE_ENTER" even though _setup_zmq subscribed using the configured
    topic name, so a renamed topic would connect but silently never fire."""
    proc = _make_process(zone_enter_topic="CUSTOM_ENTER")
    handled = []
    proc._handle_zone_enter = lambda data: handled.append(data)
    proc._zmq_socket = _FakeZmqSocket(
        [[b"CUSTOM_ENTER", json.dumps({"obj_id": 3}).encode("utf-8")]]
    )

    proc._zmq_poll_task(task=None)

    assert handled == [{"obj_id": 3}]
