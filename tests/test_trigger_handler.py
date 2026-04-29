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


# ---------------------------------------------------------------------------
# Frustum zone tests
# ---------------------------------------------------------------------------

def configure_frustum_trigger(h):
    """Wire up a frustum zone for testing without touching config files."""
    h.fov_frustum = True
    # near plane: ±0.03 at z=0.10
    h._near_z = 0.10
    h._near_x_min = -0.03
    h._near_x_max = 0.03
    h._near_y_min = -0.03
    h._near_y_max = 0.03
    # far plane: ±0.06 at z=0.30
    h._far_z = 0.30
    h._far_x_min = -0.06
    h._far_x_max = 0.06
    h._far_y_min = -0.06
    h._far_y_max = 0.06
    h.z_min = 0.10
    h.z_max = 0.30


@pytest.fixture
def frustum_handler(config_path):
    trigger = TriggerHandler(config_path=config_path)
    trigger.publisher = FakePublisher()
    configure_test_trigger(trigger)
    configure_frustum_trigger(trigger)
    return trigger


def test_frustum_get_fov_at_near_z(frustum_handler):
    h = frustum_handler
    x_min, x_max, y_min, y_max = h._get_fov_at_z(0.10)
    assert x_min == pytest.approx(-0.03)
    assert x_max == pytest.approx(0.03)


def test_frustum_get_fov_at_far_z(frustum_handler):
    h = frustum_handler
    x_min, x_max, y_min, y_max = h._get_fov_at_z(0.30)
    assert x_min == pytest.approx(-0.06)
    assert x_max == pytest.approx(0.06)


def test_frustum_get_fov_interpolates_midpoint(frustum_handler):
    h = frustum_handler
    x_min, x_max, y_min, y_max = h._get_fov_at_z(0.20)
    assert x_min == pytest.approx(-0.045)
    assert x_max == pytest.approx(0.045)


def test_frustum_inside_near_plane_accepted(frustum_handler):
    # At z=0.10 the FOV is ±0.03; a point at x=0.02 should be inside
    assert frustum_handler.is_in_trigger_zone(0.02, 0.0, 0.10)


def test_frustum_inside_far_plane_accepted(frustum_handler):
    # At z=0.30 the FOV is ±0.06; a point at x=0.05 should be inside
    assert frustum_handler.is_in_trigger_zone(0.05, 0.0, 0.30)


def test_frustum_outside_near_fov_rejected(frustum_handler):
    # At z=0.10 the FOV is ±0.03; x=0.04 is outside even though it's
    # inside the old flat box (±0.05 set by configure_test_trigger).
    assert not frustum_handler.is_in_trigger_zone(0.04, 0.0, 0.10)


def test_frustum_inside_box_but_outside_frustum_at_mid_z_rejected(frustum_handler):
    # At z=0.20 the FOV is ±0.045; x=0.05 is inside the old flat box
    # (±0.05) but outside the frustum.
    assert not frustum_handler.is_in_trigger_zone(0.05, 0.0, 0.20)
