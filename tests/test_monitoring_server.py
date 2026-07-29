import pytest

import src.monitoring.server as server_module


class _FakeSubscriber:
    def __init__(self):
        self.subscribed_topics = []
        self.connected_address = None
        self._call_count = 0

    def connect(self, address):
        self.connected_address = address

    def setsockopt_string(self, option, value):
        self.subscribed_topics.append(value)

    def recv_multipart(self):
        self._call_count += 1
        if self._call_count == 1:
            return b"CUSTOM_ENTER", b'{"obj_id": 5}'
        raise RuntimeError("stop-test-loop")


class _FakeContext:
    def __init__(self):
        self.socket_created = None

    def socket(self, kind):
        self.socket_created = _FakeSubscriber()
        return self.socket_created


def test_zmq_listener_subscribes_to_and_processes_configured_topic(monkeypatch):
    monkeypatch.setattr(server_module, "trigger_data", {"count": 0, "triggers": []})
    monkeypatch.setattr(server_module, "client_queues", {})
    fake_ctx = _FakeContext()
    monkeypatch.setattr(server_module.zmq, "Context", lambda: fake_ctx)

    with pytest.raises(RuntimeError, match="stop-test-loop"):
        server_module.zmq_listener(
            zmq_address="tcp://localhost:9999", zone_enter_topic="CUSTOM_ENTER"
        )

    assert fake_ctx.socket_created.subscribed_topics == ["CUSTOM_ENTER"]
    assert fake_ctx.socket_created.connected_address == "tcp://localhost:9999"
    assert server_module.trigger_data["count"] == 1
    assert server_module.trigger_data["triggers"] == [{"obj_id": 5}]


class _ScriptedSubscriber:
    """Replays a fixed list of recv_multipart() results, then stops the loop."""

    def __init__(self, results):
        self._results = list(results)
        self.subscribed_topics = []

    def connect(self, address):
        pass

    def setsockopt_string(self, option, value):
        self.subscribed_topics.append(value)

    def recv_multipart(self):
        if not self._results:
            raise RuntimeError("stop-test-loop")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _run_listener(monkeypatch, results, topic="ZONE_ENTER"):
    # Reset the module's real trigger_data rather than substituting a plain
    # dict-of-list: the bounded history is a property of the real structure,
    # and a stand-in would quietly not have it.
    server_module.trigger_data["count"] = 0
    server_module.trigger_data["triggers"].clear()
    monkeypatch.setattr(server_module, "client_queues", {})
    sub = _ScriptedSubscriber(results)
    monkeypatch.setattr(
        server_module.zmq, "Context", lambda: type("C", (), {"socket": lambda s, k: sub})()
    )
    with pytest.raises(RuntimeError, match="stop-test-loop"):
        server_module.zmq_listener(zmq_address="tcp://localhost:9999", zone_enter_topic=topic)
    return server_module.trigger_data


def test_malformed_json_does_not_kill_the_listener(monkeypatch):
    """One bad message used to end the daemon thread. Flask kept serving, so
    the dashboard stayed up and simply stopped updating -- forever, with
    nothing logged. A monitor that fails silently-but-alive is worse than one
    that visibly dies, because the operator concludes no flies are
    triggering."""
    data = _run_listener(
        monkeypatch,
        [
            (b"ZONE_ENTER", b"{not valid json"),
            (b"ZONE_ENTER", b'{"obj_id": 11}'),
        ],
    )

    assert data["count"] == 1, "the good message after the bad one must still arrive"
    assert list(data["triggers"]) == [{"obj_id": 11}]


def test_non_utf8_topic_does_not_kill_the_listener(monkeypatch):
    data = _run_listener(
        monkeypatch,
        [
            (b"\xff\xfe", b'{"obj_id": 1}'),
            (b"ZONE_ENTER", b'{"obj_id": 12}'),
        ],
    )

    assert list(data["triggers"]) == [{"obj_id": 12}]


def test_single_part_message_does_not_kill_the_listener(monkeypatch):
    data = _run_listener(
        monkeypatch,
        [
            (b"ZONE_ENTER",),
            (b"ZONE_ENTER", b'{"obj_id": 13}'),
        ],
    )

    assert list(data["triggers"]) == [{"obj_id": 13}]


def test_messages_on_other_topics_are_ignored(monkeypatch):
    data = _run_listener(
        monkeypatch,
        [
            (b"ZONE_EXIT", b'{"obj_id": 99}'),
            (b"ZONE_ENTER", b'{"obj_id": 14}'),
        ],
    )

    assert list(data["triggers"]) == [{"obj_id": 14}]


def test_trigger_history_is_bounded(monkeypatch):
    """/api/triggers returns the whole list on every poll; an unbounded list
    over a 24-hour run makes that response grow without limit."""
    results = [(b"ZONE_ENTER", b'{"obj_id": %d}' % i) for i in range(600)]
    data = _run_listener(monkeypatch, results)

    assert data["count"] == 600, "the running count must not be capped"
    assert len(data["triggers"]) <= 500, "the retained history must be bounded"
    assert list(data["triggers"])[-1] == {"obj_id": 599}


def test_api_triggers_is_json_serializable():
    """The history is a deque, which jsonify cannot serialize -- /api/triggers
    must convert it or every dashboard poll 500s."""
    server_module.trigger_data["count"] = 0
    server_module.trigger_data["triggers"].clear()
    server_module.trigger_data["count"] = 2
    server_module.trigger_data["triggers"].extend([{"obj_id": 1}, {"obj_id": 2}])

    with server_module.app.test_client() as client:
        response = client.get("/api/triggers")

    assert response.status_code == 200
    assert response.get_json() == {"count": 2, "triggers": [{"obj_id": 1}, {"obj_id": 2}]}
