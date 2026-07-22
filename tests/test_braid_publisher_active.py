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
                    "braid_timestamp": None,
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
                "braid_timestamp": None,
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


def test_death_for_active_object_clears_active_last_seen():
    pub = make_publisher()
    pub._active_obj_id = 7
    pub._active_last_seen = 123.0

    pub._dispatch_event(json.dumps({"msg": {"Death": 7}}))

    assert pub._active_obj_id is None
    assert pub._active_last_seen == 0.0


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
    """Regression: a new active object's own _active_last_seen -- not the
    previous trial's -- must determine whether it survives the staleness
    check in _drain_trigger_events.

    Trial A (obj 1) enters and gets one real Update, which plants a
    genuine nonzero _active_last_seen (1.0) via the matching-Update path
    in _dispatch_event -- the only pre-fix path that ever sets it to a
    real value. Trial A then ends without a ZONE_EXIT (e.g. the tracker
    lost it), and trial B (obj 2) enters shortly after.

    Before the fix, obj B's ZONE_ENTER only set _active_obj_id, leaving
    _active_last_seen at trial A's stale value (1.0). By clock 4.5 --
    less than zone_timeout since B actually started, but more than
    zone_timeout since A's last real Update -- the staleness check would
    wrongly expire B. Post-fix, B's own _active_last_seen (2.0, set on
    its ZONE_ENTER) keeps it alive."""
    pub = make_publisher()
    pub.config.zone_timeout = 3.0
    clock = {"t": 0.0}
    monkeypatch.setattr("src.processes.braid.time.monotonic", lambda: clock["t"])

    # Trial A: object 1 enters and receives one real Update -- the only
    # pre-fix path that ever sets _active_last_seen to a genuine nonzero
    # value.
    pub._handle_trigger_message("ZONE_ENTER", {"obj_id": 1})
    clock["t"] = 1.0
    pub._dispatch_event(json.dumps({"msg": {"Update": {"obj_id": 1, "x": 0, "y": 0, "z": 0}}}))
    assert pub._active_last_seen == 1.0  # sanity: trial A's clock is real

    # Trial A ends without a ZONE_EXIT (e.g. tracker lost it) and, after
    # the typical cooldown, trial B's ZONE_ENTER arrives.
    clock["t"] = 2.0
    pub.trigger_socket.messages = [
        [b"ZONE_ENTER", json.dumps({"obj_id": 2}).encode("utf-8")],
    ]
    pub._drain_trigger_events()
    assert pub._active_obj_id == 2

    # Later -- less than zone_timeout since B actually started, but more
    # than zone_timeout since A's last real Update -- drain again.
    # Pre-fix, B inherited A's stale _active_last_seen (1.0) and is
    # wrongly expired here; post-fix, B's own last_seen (2.0) keeps it
    # alive.
    clock["t"] = 4.5
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


def test_dispatch_event_injects_braid_timestamp_from_envelope(monkeypatch):
    pub = make_publisher()
    monkeypatch.setattr("src.processes.braid.time.time", lambda: 123.0)

    pub._dispatch_event(
        json.dumps(
            {
                "trigger_timestamp": 999.5,
                "msg": {
                    "Update": {"obj_id": 7, "frame": 12, "x": 0.1, "y": 0.2, "z": 0.3}
                },
            }
        )
    )

    sent = sent_payloads(pub.zmq_socket)
    assert sent[0][1]["Update"]["braid_timestamp"] == 999.5
    assert sent[0][1]["Update"]["t_relay"] == 123.0


def test_dispatch_event_treats_nan_trigger_timestamp_as_none():
    pub = make_publisher()

    pub._dispatch_event(
        json.dumps(
            {
                "trigger_timestamp": float("nan"),
                "msg": {
                    "Update": {"obj_id": 7, "frame": 12, "x": 0.1, "y": 0.2, "z": 0.3}
                },
            }
        )
    )

    sent = sent_payloads(pub.zmq_socket)
    assert sent[0][1]["Update"]["braid_timestamp"] is None


def test_dispatch_event_missing_trigger_timestamp_injects_none():
    pub = make_publisher()

    pub._dispatch_event(
        json.dumps(
            {"msg": {"Update": {"obj_id": 7, "frame": 12, "x": 0.1, "y": 0.2, "z": 0.3}}}
        )
    )

    sent = sent_payloads(pub.zmq_socket)
    assert sent[0][1]["Update"]["braid_timestamp"] is None
