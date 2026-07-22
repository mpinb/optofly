import json
import multiprocessing as mp

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
    pub._active_last_seen = 0.0
    pub.config = type(
        "Config",
        (),
        {
            "zone_timeout": 3.0,
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
        {
            "debug": lambda *args, **kwargs: None,
            "error": lambda *args, **kwargs: None,
            "warning": lambda *args, **kwargs: None,
        },
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


def make_uninitialized_publisher(event):
    """A BraidPublisher that never got past __init__ / never connected —
    matching the state close() must handle when called from initialize()'s
    failure path, where nothing has been set up yet."""
    pub = object.__new__(BraidPublisher)
    pub.stop_event = event
    pub.stream_thread = None
    pub.zmq_socket = None
    pub.active_braid_socket = None
    pub.trigger_socket = None
    pub.zmq_context = None
    pub.session = None
    pub.is_connected = False
    pub.logger = type(
        "Logger",
        (),
        {
            "debug": lambda *a, **k: None,
            "info": lambda *a, **k: None,
            "error": lambda *a, **k: None,
        },
    )()
    return pub


def test_zone_enter_seeds_active_last_seen(monkeypatch):
    pub = make_publisher()
    monkeypatch.setattr("src.processes.braid.time.monotonic", lambda: 500.0)

    pub._handle_trigger_message("ZONE_ENTER", {"obj_id": 9})

    assert pub._active_obj_id == 9
    assert pub._active_last_seen == 500.0


def test_reentry_after_prior_trial_is_not_immediately_expired(monkeypatch):
    """Regression: a new active object must not inherit the previous
    trial's stale _active_last_seen. Before the fix, obj B's ZONE_ENTER
    only set _active_obj_id, leaving _active_last_seen at whatever it was
    from trial A -- since cooldown_period is typically well above
    zone_timeout, the very next _drain_trigger_events() call would see
    `age > zone_timeout` and immediately clear B before any Update for it
    ever arrived."""
    pub = make_publisher()
    pub.config.zone_timeout = 3.0
    clock = {"t": 0.0}
    monkeypatch.setattr("src.processes.braid.time.monotonic", lambda: clock["t"])

    pub._handle_trigger_message("ZONE_ENTER", {"obj_id": 1})
    clock["t"] = 20.0  # well past zone_timeout and typical cooldown_period

    pub.trigger_socket.messages = [
        [b"ZONE_ENTER", json.dumps({"obj_id": 2}).encode("utf-8")],
    ]
    pub._drain_trigger_events()

    assert pub._active_obj_id == 2


def test_zone_exit_clears_active_last_seen():
    pub = make_publisher()
    pub._active_obj_id = 7
    pub._active_last_seen = 123.0

    pub._handle_trigger_message("ZONE_EXIT", {"obj_id": 7})

    assert pub._active_obj_id is None
    assert pub._active_last_seen == 0.0


def test_close_does_not_set_shared_stop_event():
    """close() must not cascade a stop signal to sibling processes: it runs
    both after a normal shutdown (the shared event is already set by
    whoever requested the stop) and from initialize()'s failure path,
    where forcing every other worker to shut down too would misattribute
    a BraidPublisher-only failure to whichever critical process happens
    to be checked first."""
    event = mp.Event()
    pub = make_uninitialized_publisher(event)

    assert not event.is_set()
    pub.close()
    assert not event.is_set()
