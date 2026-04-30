import json

import zmq

from src.processes.braid import BraidPublisher


class FakeSocket:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.sent = []
        self.options = []

    def recv_multipart(self, flags=0):
        if not self.messages:
            raise zmq.Again()
        return self.messages.pop(0)

    def send_multipart(self, parts):
        self.sent.append(parts)

    def setsockopt(self, option, value):
        self.options.append((option, value))

    def setsockopt_string(self, option, value):
        self.options.append((option, value))


def make_publisher():
    pub = object.__new__(BraidPublisher)
    pub.zmq_socket = FakeSocket()
    pub.active_braid_socket = FakeSocket()
    pub.trigger_socket = FakeSocket()
    pub._topic_bytes = b"BRAID"
    pub._active_topic_bytes = b"ACTIVE_BRAID"
    pub._active_obj_id = None
    pub.config = type(
        "Config",
        (),
        {
            "zmq": type(
                "ZMQ",
                (),
                {
                    "zone_enter_topic": "ZONE_ENTER",
                    "zone_exit_topic": "ZONE_EXIT",
                },
            )()
        },
    )()
    pub.logger = type(
        "Logger",
        (),
        {"debug": lambda *args, **kwargs: None, "error": lambda *args, **kwargs: None},
    )()
    return pub


def sent_payloads(socket):
    return [(topic.decode("utf-8"), json.loads(payload)) for topic, payload in socket.sent]


def test_trigger_messages_set_and_clear_active_object():
    pub = make_publisher()
    pub.trigger_socket.messages = [
        [b"ZONE_ENTER", json.dumps({"obj_id": 7}).encode("utf-8")],
        [b"ZONE_EXIT", json.dumps({"obj_id": 8}).encode("utf-8")],
        [b"ZONE_EXIT", json.dumps({"obj_id": 7}).encode("utf-8")],
    ]

    pub._drain_trigger_events()

    assert pub._active_obj_id is None


def test_matching_update_is_published_to_full_and_active_feeds(monkeypatch):
    pub = make_publisher()
    pub._active_obj_id = 7
    monkeypatch.setattr("src.processes.braid.time.time", lambda: 123.0)

    pub._dispatch_event(
        json.dumps(
            {
                "msg": {
                    "Update": {
                        "obj_id": 7,
                        "frame": 12,
                        "x": 0.1,
                        "y": 0.2,
                        "z": 0.3,
                    }
                }
            }
        )
    )

    assert sent_payloads(pub.zmq_socket) == [
        (
            "BRAID",
            {
                "Update": {
                    "obj_id": 7,
                    "frame": 12,
                    "x": 0.1,
                    "y": 0.2,
                    "z": 0.3,
                    "t_relay": 123.0,
                }
            },
        )
    ]
    assert sent_payloads(pub.active_braid_socket) == [
        (
            "ACTIVE_BRAID",
            {
                "obj_id": 7,
                "frame": 12,
                "x": 0.1,
                "y": 0.2,
                "z": 0.3,
                "t_relay": 123.0,
            },
        )
    ]


def test_non_matching_update_only_uses_full_feed():
    pub = make_publisher()
    pub._active_obj_id = 7

    pub._dispatch_event(json.dumps({"msg": {"Update": {"obj_id": 8, "frame": 12}}}))

    assert len(pub.zmq_socket.sent) == 1
    assert pub.active_braid_socket.sent == []


def test_death_for_active_object_clears_active_state():
    pub = make_publisher()
    pub._active_obj_id = 7

    pub._dispatch_event(json.dumps({"msg": {"Death": 7}}))

    assert pub._active_obj_id is None
