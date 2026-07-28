import json
import math
from pathlib import Path

import pytest

import src.processes.tracking as tracking_module
from src.processes.tracking import TrackedObject, TriggerHandler, _should_run_cleanup
from src.utils.config import CameraConfig, TriggerHandlerConfig


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
    # Create a test config with custom values instead of mutating the frozen config
    # Start with the default camera and zmq from the handler's current config
    camera = CameraConfig.from_section(
        {
            "active": True,
            "resolution": [640, 480],
            "fps": 100,
            "FOV": {
                "x_min": -0.05,
                "x_max": 0.05,
                "y_min": -0.05,
                "y_max": 0.05,
            },
        }
    )
    zmq = handler.config.zmq

    # Build test config with custom values
    test_config = TriggerHandlerConfig.from_section(
        {
            "zone_timeout": 0.5,
            "cooldown_period": 0.0,
            "z_min": 0.1,
            "z_max": 0.3,
            "min_tracking_age": 0.0,
            "min_velocity": 0.05,
            "max_velocity": 0.5,
            "heading_cone_deg": 30.0,
        },
        camera=camera,
        zmq=zmq,
    )

    # Replace the handler's config with the test config
    handler.config = test_config

    # Update the handler's mirrored attributes from the new config
    handler.fov_x_min = test_config.fov_x_min
    handler.fov_x_max = test_config.fov_x_max
    handler.fov_y_min = test_config.fov_y_min
    handler.fov_y_max = test_config.fov_y_max
    handler.fov_center_x = (test_config.fov_x_min + test_config.fov_x_max) / 2.0
    handler.fov_center_y = (test_config.fov_y_min + test_config.fov_y_max) / 2.0
    handler.z_min = test_config.z_min
    handler.z_max = test_config.z_max
    handler.cooldown_period = test_config.cooldown_period


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
    braid_timestamp=None,
):
    track = {
        "obj_id": obj_id,
        "frame": frame,
        "x": x,
        "y": y,
        "z": z,
        "xvel": xvel,
        "yvel": yvel,
        "zvel": zvel,
    }
    if braid_timestamp is not None:
        track["braid_timestamp"] = braid_timestamp
    return track


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


def test_heading_gate_targets_camera_fov_center(handler):
    handler.fov_x_min = 0.10
    handler.fov_x_max = 0.20
    handler.fov_y_min = -0.05
    handler.fov_y_max = 0.05
    handler.fov_center_x = 0.15
    handler.fov_center_y = 0.0

    handler.process_message(make_birth(x=0.08, frame=99, xvel=0.2))
    handler.process_message(make_update(x=0.10, frame=100, xvel=0.2))

    messages = zone_messages(handler.publisher)
    assert [topic for topic, _ in messages] == ["ZONE_ENTER"]


def test_exit_emitted_when_object_leaves_zone(handler):
    handler.process_message(make_birth(x=0.08, frame=99, xvel=-0.2))
    handler.process_message(make_update(x=0.05, frame=100, xvel=-0.2))
    handler.process_message(make_update(x=0.08, frame=101, xvel=-0.2))

    assert [topic for topic, _ in zone_messages(handler.publisher)] == [
        "ZONE_ENTER",
        "ZONE_EXIT",
    ]


def test_reentry_waits_for_cooldown_period_before_second_enter(handler, fake_clock):
    # Create a new config with longer cooldown_period for this test
    camera = CameraConfig.from_section(
        {
            "active": True,
            "resolution": [640, 480],
            "fps": 100,
            "FOV": {
                "x_min": handler.config.fov_x_min,
                "x_max": handler.config.fov_x_max,
                "y_min": handler.config.fov_y_min,
                "y_max": handler.config.fov_y_max,
            },
        }
    )
    test_config = TriggerHandlerConfig.from_section(
        {
            "zone_timeout": handler.config.zone_timeout,
            "cooldown_period": 5.0,
            "z_min": handler.config.z_min,
            "z_max": handler.config.z_max,
            "min_tracking_age": handler.config.min_tracking_age,
            "min_velocity": handler.config.min_velocity,
            "max_velocity": handler.config.max_velocity,
            "heading_cone_deg": handler.config.heading_cone_deg,
        },
        camera=camera,
        zmq=handler.config.zmq,
    )
    handler.config = test_config
    handler.cooldown_period = 5.0

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
    # Create a new config with shorter zone_timeout for this test
    camera = CameraConfig.from_section(
        {
            "active": True,
            "resolution": [640, 480],
            "fps": 100,
            "FOV": {
                "x_min": handler.config.fov_x_min,
                "x_max": handler.config.fov_x_max,
                "y_min": handler.config.fov_y_min,
                "y_max": handler.config.fov_y_max,
            },
        }
    )
    test_config = TriggerHandlerConfig.from_section(
        {
            "zone_timeout": 0.05,
            "cooldown_period": handler.config.cooldown_period,
            "z_min": handler.config.z_min,
            "z_max": handler.config.z_max,
            "min_tracking_age": handler.config.min_tracking_age,
            "min_velocity": handler.config.min_velocity,
            "max_velocity": handler.config.max_velocity,
            "heading_cone_deg": handler.config.heading_cone_deg,
        },
        camera=camera,
        zmq=handler.config.zmq,
    )
    handler.config = test_config

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


def test_should_run_cleanup_throttled_when_nothing_in_zone():
    objects = {1: TrackedObject(obj_id=1, first_timestamp=0.0)}
    assert _should_run_cleanup(objects, elapsed_since_last=1.0) is False
    assert _should_run_cleanup(objects, elapsed_since_last=5.1) is True


def test_should_run_cleanup_every_iteration_when_object_in_zone():
    """A fly that vanishes mid-trial should trigger ZONE_EXIT close to
    zone_timeout, not up to `interval` seconds later on top of it."""
    obj = TrackedObject(obj_id=1, first_timestamp=0.0)
    obj.in_zone = True
    objects = {1: obj}
    assert _should_run_cleanup(objects, elapsed_since_last=0.01) is True


def test_should_run_cleanup_with_no_tracked_objects():
    assert _should_run_cleanup({}, elapsed_since_last=1.0) is False


def test_zone_enter_preserves_braid_timestamp_and_adds_handler_timestamp(
    handler, fake_clock
):
    handler.process_message(
        make_birth(x=0.08, frame=99, xvel=-0.2, braid_timestamp=500.0)
    )
    fake_clock.advance(0.05)
    handler.process_message(
        make_update(x=0.05, frame=100, xvel=-0.2, braid_timestamp=500.05)
    )

    messages = zone_messages(handler.publisher)
    assert [topic for topic, _ in messages] == ["ZONE_ENTER"]
    payload = messages[0][1]
    assert payload["braid_timestamp"] == 500.05
    assert payload["handler_timestamp"] == fake_clock.now
    assert payload["timestamp"] == fake_clock.now  # existing field unchanged


def all_messages(fake_publisher):
    return decode_messages(fake_publisher)


def test_opto_zone_enter_fires_once_fly_reaches_scaled_inner_zone(handler):
    handler.config = handler.config.__class__.from_section(
        {
            "zone_timeout": handler.config.zone_timeout,
            "cooldown_period": handler.config.cooldown_period,
            "z_min": handler.config.z_min,
            "z_max": handler.config.z_max,
            "min_tracking_age": handler.config.min_tracking_age,
            "min_velocity": handler.config.min_velocity,
            "max_velocity": handler.config.max_velocity,
            "heading_cone_deg": handler.config.heading_cone_deg,
            "opto_zone_scale": 0.5,
            "visual_zone_scale": 1.0,
        },
        camera=CameraConfig.from_section(
            {
                "active": True,
                "resolution": [640, 480],
                "fps": 100,
                "FOV": {
                    "x_min": handler.config.fov_x_min,
                    "x_max": handler.config.fov_x_max,
                    "y_min": handler.config.fov_y_min,
                    "y_max": handler.config.fov_y_max,
                },
            }
        ),
        zmq=handler.config.zmq,
    )
    handler.opto_zone_scale = 0.5
    handler.visual_zone_scale = 1.0

    # FOV is -0.05..0.05 in x/y (see configure_test_trigger). Outer entry at
    # x=0.05 is inside the outer FOV (triggers ZONE_ENTER + VISUAL_ZONE_ENTER,
    # since visual_zone_scale=1.0 makes the visual box equal the outer FOV)
    # but outside the opto zone (half-width 0.05*0.5=0.025 at scale=0.5).
    # Only once x reaches 0.0 (fov center) is the fly guaranteed inside the
    # scaled-down opto box, firing OPTO_ZONE_ENTER on that later update.
    handler.process_message(make_birth(x=0.08, frame=1, xvel=-0.2))
    handler.process_message(make_update(x=0.05, frame=2, xvel=-0.2))
    handler.process_message(make_update(x=0.0, frame=3, xvel=-0.2))

    messages = all_messages(handler.publisher)
    topics = [topic for topic, _ in messages]
    assert topics == ["ZONE_ENTER", "VISUAL_ZONE_ENTER", "OPTO_ZONE_ENTER"]
    opto_payload = messages[2][1]
    assert opto_payload["obj_id"] == 7
    assert opto_payload["frame"] == 3
    assert opto_payload["record_frame"] == 2


def test_visual_zone_enter_fires_same_frame_as_zone_enter_when_scale_is_one(handler):
    handler.visual_zone_scale = 1.0

    handler.process_message(make_birth(x=0.08, frame=1, xvel=-0.2))
    handler.process_message(make_update(x=0.05, frame=2, xvel=-0.2))

    messages = all_messages(handler.publisher)
    assert [topic for topic, _ in messages] == ["ZONE_ENTER", "VISUAL_ZONE_ENTER"]
    assert messages[0][1]["frame"] == messages[1][1]["frame"] == 2
    assert messages[1][1]["record_frame"] == 2


def test_opto_and_visual_fired_flags_reset_on_zone_exit_allowing_refire(handler):
    handler.opto_zone_scale = 1.0
    handler.visual_zone_scale = 1.0

    handler.process_message(make_birth(x=0.08, frame=1, xvel=-0.2))
    handler.process_message(make_update(x=0.05, frame=2, xvel=-0.2))
    handler.process_message(make_update(x=0.08, frame=3, xvel=-0.2))  # exits FOV
    handler.process_message(make_update(x=0.05, frame=4, xvel=-0.2))  # re-enters

    # Both scales are 1.0 here, so opto and visual both fire on the same
    # update as ZONE_ENTER; the implementation checks opto before visual
    # when both fire simultaneously (see Step 7 below), hence this order.
    topics = [topic for topic, _ in all_messages(handler.publisher)]
    assert topics == [
        "ZONE_ENTER",
        "OPTO_ZONE_ENTER",
        "VISUAL_ZONE_ENTER",
        "ZONE_EXIT",
        "ZONE_ENTER",
        "OPTO_ZONE_ENTER",
        "VISUAL_ZONE_ENTER",
    ]


def test_opto_zone_enter_never_fires_before_zone_enter(handler):
    handler.opto_zone_scale = 1.0
    handler.visual_zone_scale = 1.0

    # xvel=0 with min_velocity=0.05 means the object never passes the
    # velocity gate, so it should never reach in_zone at all.
    handler.process_message(make_birth(x=0.08, frame=1, xvel=0.0))
    handler.process_message(make_update(x=0.05, frame=2, xvel=0.0))

    assert all_messages(handler.publisher) == []


def test_zone_enter_payload_includes_record_frame_equal_to_its_own_frame(handler):
    handler.process_message(make_birth(x=0.08, frame=1, xvel=-0.2))
    handler.process_message(make_update(x=0.05, frame=2, xvel=-0.2))

    messages = zone_messages(handler.publisher)
    zone_enter_payload = messages[0][1]
    assert zone_enter_payload["record_frame"] == zone_enter_payload["frame"] == 2


def test_get_zone_at_z_scale_one_equals_outer_fov(handler):
    x_min, x_max, y_min, y_max = handler._get_zone_at_z(0.2, 1.0)
    assert (x_min, x_max, y_min, y_max) == handler._get_fov_at_z(0.2)


def test_get_zone_at_z_shrinks_toward_center(handler):
    outer = handler._get_fov_at_z(0.2)
    x_min, x_max, y_min, y_max = handler._get_zone_at_z(0.2, 0.5)
    outer_cx = (outer[0] + outer[1]) / 2.0
    outer_cy = (outer[2] + outer[3]) / 2.0
    assert x_min == pytest.approx(outer_cx - (outer[1] - outer[0]) / 4.0)
    assert x_max == pytest.approx(outer_cx + (outer[1] - outer[0]) / 4.0)
    assert y_min == pytest.approx(outer_cy - (outer[3] - outer[2]) / 4.0)
    assert y_max == pytest.approx(outer_cy + (outer[3] - outer[2]) / 4.0)


def test_get_zone_at_z_scales_frustum_interpolated_box(frustum_handler):
    """Uses the frustum_handler fixture already defined earlier in this
    file (near ±0.03 at z=0.10, far ±0.06 at z=0.30, both centered on 0),
    so a 0.5 scale should exactly halve the frustum-interpolated box at
    either plane."""
    h = frustum_handler
    x_min, x_max, y_min, y_max = h._get_zone_at_z(0.10, 0.5)
    assert x_min == pytest.approx(-0.015)
    assert x_max == pytest.approx(0.015)
    assert y_min == pytest.approx(-0.015)
    assert y_max == pytest.approx(0.015)

    x_min, x_max, y_min, y_max = h._get_zone_at_z(0.30, 0.5)
    assert x_min == pytest.approx(-0.03)
    assert x_max == pytest.approx(0.03)
    assert y_min == pytest.approx(-0.03)
    assert y_max == pytest.approx(0.03)


def test_velocity_and_age_bookkeeping_still_use_receipt_clock_not_braid_timestamp(
    handler, fake_clock
):
    """Regression guard: Braid's own timestamp must never leak into
    TriggerHandler's internal age/cooldown/velocity math, which relies on
    its own receipt-time clock and would break if Braid's clock isn't
    synced to the local machine."""
    handler.process_message(
        make_birth(frame=1, x=0.08, xvel=-0.2, braid_timestamp=1_000_000.0)
    )

    tracked = handler.tracked_objects[7]
    assert tracked.get_tracking_duration(fake_clock.now) == 0.0
    assert tracked.current_braid_timestamp == 1_000_000.0
