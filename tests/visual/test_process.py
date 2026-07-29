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


def _make_process(visual_enter_topic):
    proc = object.__new__(VisualProcess)
    proc.stop_event = type("Event", (), {"is_set": lambda self: False})()
    proc._visual_enter_topic = visual_enter_topic
    proc._zmq_socket = None
    proc.logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "exception": lambda *a, **k: None},
    )()
    return proc


def test_zmq_poll_task_uses_configured_visual_enter_topic_not_literal():
    """A renamed zmq.visual_enter_topic in config must still be recognized --
    _zmq_poll_task must compare against the configured topic name, not a
    hardcoded literal."""
    proc = _make_process(visual_enter_topic="CUSTOM_ENTER")
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
    proc = _make_process(visual_enter_topic="VISUAL_ZONE_ENTER")
    proc._stimuli = []  # no registered stimulus -> stim_params stays empty
    proc._csv_writer = None
    proc._offset_rad = 0.0
    proc._flip = False
    proc._latency_socket = FakeLatencySocket()

    proc._handle_zone_enter(
        {
            "obj_id": 7,
            "frame": 100,
            "record_frame": 95,
            "braid_timestamp": 500.0,
            "handler_timestamp": 500.01,
        }
    )

    sent = proc._latency_socket.sent
    assert len(sent) == 1
    assert sent[0]["system"] == "visual"
    assert sent[0]["sham"] is True
    assert sent[0]["activation_timestamp"] is None
    assert sent[0]["braid_timestamp"] == 500.0
    assert sent[0]["trigger_timestamp"] == 500.01
    assert sent[0]["record_frame"] == 95


def test_handle_zone_enter_publishes_latency_with_real_activation_when_a_stimulus_fires(
    monkeypatch,
):
    proc = _make_process(visual_enter_topic="VISUAL_ZONE_ENTER")

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


def test_setup_zmq_stores_configured_visual_enter_topic():
    """Exercises the real _setup_zmq() path against the checked-in example
    config to confirm it stores visual_enter_topic, not zone_enter_topic."""
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
        from src.utils.config import AppConfig

        zmq_cfg = AppConfig.load("configs/config.example.toml").zmq
        assert proc._visual_enter_topic == zmq_cfg.visual_enter_topic
        assert proc._visual_enter_topic != zmq_cfg.zone_enter_topic
    finally:
        proc._latency_socket.close()
        proc._zmq_socket.close()
        proc._zmq_context.term()


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


def _config_pointing_at(tmp_path, visual_stimuli_path):
    """A main config whose visual_stimuli.config_file points where we say."""
    source = open("configs/config.example.toml").read().replace(
        'config_file = "configs/visual_stimuli.toml"',
        f'config_file = "{visual_stimuli_path}"',
    )
    out = tmp_path / "config.toml"
    out.write_text(source)
    return out


def test_missing_visual_stimuli_file_fails_loudly(tmp_path):
    """Forgetting `cp configs/visual_stimuli.example.toml ...` used to fall
    back to the main config's [visual_stimuli] section -- which holds only
    `active` and `config_file` -- so background.enabled defaulted to True and
    everything else to False. The user got a grey screen with random squares,
    no looming, and nothing in the logs. Silently running the wrong stimuli
    invalidates an experiment rather than failing it."""
    import pytest

    config_path = _config_pointing_at(tmp_path, tmp_path / "not_created.toml")
    proc = object.__new__(VisualProcess)
    proc._config_path = str(config_path)

    with pytest.raises(FileNotFoundError) as exc:
        proc._load_config()

    message = str(exc.value)
    assert "not_created.toml" in message
    assert "cp configs/visual_stimuli.example.toml" in message, (
        f"must name the command that fixes it: {message}"
    )


def test_present_visual_stimuli_file_is_loaded(tmp_path):
    stimuli = tmp_path / "visual_stimuli.toml"
    stimuli.write_text(
        "[visual_stimuli]\nlog_file = \"stim.csv\"\n\n"
        "[visual_stimuli.looming]\nenabled = true\ninitial_size_deg = 7.5\n"
    )
    config_path = _config_pointing_at(tmp_path, stimuli)
    proc = object.__new__(VisualProcess)
    proc._config_path = str(config_path)

    cfg = proc._load_config()

    assert cfg["looming"]["enabled"] is True
    assert cfg["looming"]["initial_size_deg"] == 7.5
