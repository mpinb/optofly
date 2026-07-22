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
