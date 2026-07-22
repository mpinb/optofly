import json
import math

import zmq

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


class FakeLatencySocket:
    def __init__(self):
        self.sent = []

    def send(self, raw):
        self.sent.append(json.loads(raw))


def test_handle_zone_enter_publishes_latency_with_sham_true_when_no_stimulus_fires():
    proc = _make_process(zone_enter_topic="ZONE_ENTER")
    proc._stimuli = []  # no registered stimulus -> stim_params stays empty
    proc._csv_writer = None
    proc._offset_rad = 0.0
    proc._flip = False
    proc._latency_socket = FakeLatencySocket()

    proc._handle_zone_enter(
        {"obj_id": 7, "frame": 100, "braid_timestamp": 500.0, "handler_timestamp": 500.01}
    )

    sent = proc._latency_socket.sent
    assert len(sent) == 1
    assert sent[0]["system"] == "visual"
    assert sent[0]["sham"] is True
    assert sent[0]["activation_timestamp"] is None
    assert sent[0]["braid_timestamp"] == 500.0
    assert sent[0]["trigger_timestamp"] == 500.01


def test_handle_zone_enter_publishes_latency_with_real_activation_when_a_stimulus_fires(
    monkeypatch,
):
    proc = _make_process(zone_enter_topic="ZONE_ENTER")

    class _AlwaysFiresStimulus:
        def on_trigger(self, heading_deg, trigger_data):
            return {"looming_sham": False}

    proc._stimuli = [_AlwaysFiresStimulus()]
    proc._csv_writer = None
    proc._offset_rad = 0.0
    proc._flip = False
    proc._latency_socket = FakeLatencySocket()
    monkeypatch.setattr("src.visual.process.time.time", lambda: 999.0)

    proc._handle_zone_enter(
        {"obj_id": 7, "frame": 100, "braid_timestamp": 500.0, "handler_timestamp": 500.01}
    )

    sent = proc._latency_socket.sent
    assert sent[0]["sham"] is False
    assert sent[0]["activation_timestamp"] == 999.0


def test_setup_zmq_configures_latency_socket_as_non_blocking():
    """A dead/slow LatencyLogger must never be able to block this process's
    stimulus-rendering loop. Without SNDTIMEO=0/LINGER=0, a full
    SNDHWM=1000 queue makes the next _latency_socket.send() hang forever.
    Exercises the real _setup_zmq() setup path (not a fake socket) against
    the checked-in example config, so getsockopt reflects genuine zmq
    behavior."""
    proc = object.__new__(VisualProcess)
    proc.standalone = False
    proc._config_path = "configs/config.example.toml"
    proc.logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "debug": lambda *a, **k: None},
    )()

    proc._setup_zmq()

    try:
        assert proc._latency_socket.getsockopt(zmq.SNDTIMEO) == 0
        assert proc._latency_socket.getsockopt(zmq.LINGER) == 0
    finally:
        proc._latency_socket.close()
        proc._zmq_socket.close()
        proc._zmq_context.term()
