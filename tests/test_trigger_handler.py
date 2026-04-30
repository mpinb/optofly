import json
import math
from pathlib import Path

import pytest

import src.processes.tracking as tracking_module
from src.processes.tracking import TriggerHandler


class FakePublisher:
    def __init__(self):
        self.messages = []

    def send_multipart(self, parts):
        self.messages.append(parts)


def decode_messages(fake_publisher):
    decoded = []
    for topic_b, payload_b in fake_publisher.messages:
        decoded.append((topic_b.decode("utf-8"), json.loads(payload_b.decode("utf-8"))))
    return decoded


def zone_messages(fake_publisher):
    return [
        (topic, payload)
        for topic, payload in decode_messages(fake_publisher)
        if topic in {"ZONE_ENTER", "ZONE_EXIT"}
    ]


@pytest.fixture
def config_path():
    return str(Path("configs/config.example.toml"))


@pytest.fixture
def handler(config_path):
    trigger = TriggerHandler(config_path=config_path)
    trigger.publisher = FakePublisher()
    configure_test_trigger(trigger)
    return trigger


def configure_test_trigger(handler):
    handler.fov_x_min = -0.05
    handler.fov_x_max = 0.05
    handler.fov_y_min = -0.05
    handler.fov_y_max = 0.05
    handler.z_min = 0.1
    handler.z_max = 0.3

    handler.config.min_tracking_age = 0.0
    handler.config.min_velocity = 0.05
    handler.config.max_velocity = 0.5
    handler.config.heading_threshold = math.radians(30.0)
    handler.config.zone_timeout = 0.5
    handler.config.refractory_period = 0.0
    handler.refractory_period = 0.0


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(tracking_module.time, "time", clock.time)
    return clock


def make_track(
    obj_id=7,
    frame=100,
    x=0.0,
    y=0.0,
    z=0.2,
    xvel=0.2,
    yvel=0.0,
    zvel=0.0,
):
    return {
        "obj_id": obj_id,
        "frame": frame,
        "x": x,
        "y": y,
        "z": z,
        "xvel": xvel,
        "yvel": yvel,
        "zvel": zvel,
    }


def make_birth(**kwargs):
    return {"Birth": make_track(**kwargs)}


def make_update(**kwargs):
    return {"Update": make_track(**kwargs)}


def test_emits_zone_enter_when_all_gates_pass(handler):
    handler.process_message(make_birth(x=0.08, frame=99, xvel=-0.2))
    handler.process_message(make_update(x=0.05, frame=100, xvel=-0.2))

    messages = zone_messages(handler.publisher)
    assert [topic for topic, _ in messages] == ["ZONE_ENTER"]
    assert messages[0][1]["obj_id"] == 7
    assert messages[0][1]["xvel"] == -0.2
    assert messages[0][1]["yvel"] == 0.0
    assert messages[0][1]["zvel"] == 0.0


def test_first_seen_update_for_unknown_object_is_evaluated(handler):
    handler.process_message(make_update(obj_id=42, frame=1, x=0.05, xvel=-0.2))

    messages = zone_messages(handler.publisher)
    assert [topic for topic, _ in messages] == ["ZONE_ENTER"]
    assert messages[0][1]["obj_id"] == 42


def test_exit_emitted_when_object_leaves_zone(handler):
    handler.process_message(make_birth(x=0.08, frame=99, xvel=-0.2))
    handler.process_message(make_update(x=0.05, frame=100, xvel=-0.2))
    handler.process_message(make_update(x=0.08, frame=101, xvel=-0.2))

    assert [topic for topic, _ in zone_messages(handler.publisher)] == [
        "ZONE_ENTER",
        "ZONE_EXIT",
    ]


def test_reentry_waits_for_refractory_period_before_second_enter(handler, fake_clock):
    handler.config.refractory_period = 5.0
    handler.refractory_period = 5.0

    handler.process_message(make_birth(frame=1, x=0.08, xvel=-0.2))
    fake_clock.advance(1.0)
    handler.process_message(make_update(frame=2, x=0.05, xvel=-0.2))
    fake_clock.advance(1.0)
    handler.process_message(make_update(frame=3, x=0.08, xvel=-0.2))
    fake_clock.advance(2.0)
    handler.process_message(make_update(frame=4, x=0.05, xvel=-0.2))
    fake_clock.advance(3.0)
    handler.process_message(make_update(frame=5, x=0.04, xvel=-0.2))

    assert [topic for topic, _ in zone_messages(handler.publisher)] == [
        "ZONE_ENTER",
        "ZONE_EXIT",
        "ZONE_ENTER",
    ]


def test_heading_and_velocity_are_not_rechecked_after_zone_enter(handler):
    handler.process_message(make_birth(frame=1, x=0.08, xvel=-0.2))
    handler.process_message(make_update(frame=2, x=0.04, xvel=-0.2))

    for frame in range(3, 13):
        handler.process_message(make_update(frame=frame, x=0.04, xvel=0.6, yvel=0.0))

    assert [topic for topic, _ in zone_messages(handler.publisher)] == ["ZONE_ENTER"]
    tracked = handler.tracked_objects[7]
    mean_vel = tracked.get_mean_velocity()
    assert mean_vel is not None
    assert math.hypot(mean_vel[0], mean_vel[1]) > handler.config.max_velocity
    assert not tracked.is_heading_toward_center(handler.config.heading_threshold)
    assert tracked.in_zone is True


def test_death_emits_zone_exit(handler):
    handler.process_message(make_birth(frame=1, x=0.08, xvel=-0.2))
    handler.process_message(make_update(frame=2, x=0.05, xvel=-0.2))
    handler.process_message({"Death": 7})

    messages = zone_messages(handler.publisher)
    assert [topic for topic, _ in messages] == ["ZONE_ENTER", "ZONE_EXIT"]
    assert messages[1][1]["reason"] == "death"


def test_timeout_emits_zone_exit(handler):
    handler.config.zone_timeout = 0.05

    handler.process_message(make_birth(frame=1, x=0.08, xvel=-0.2))
    handler.process_message(make_update(frame=2, x=0.05, xvel=-0.2))
    tracked = handler.tracked_objects[7]
    tracked.last_check_time -= 1.0

    handler._cleanup_stale_objects()

    messages = zone_messages(handler.publisher)
    assert [topic for topic, _ in messages] == ["ZONE_ENTER", "ZONE_EXIT"]
    assert messages[1][1]["reason"] == "timeout"
