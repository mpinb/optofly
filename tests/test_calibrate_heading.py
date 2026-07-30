"""Tests for calibrate_heading.fit_calibration.

No hardware, Braid, or display required.
"""

import math

import pytest

from src.tools.calibrate_heading import _WORLD_RAD, fit_calibration


def _runtime_heading(xvel: float, yvel: float) -> float:
    """Reproduce tracking.py's heading formula exactly: math.atan2(yvel, xvel)."""
    return math.atan2(yvel, xvel)


def _simulate_measurements(true_offset_rad: float, true_flip: bool):
    """Build (world_rad, (braid_x, braid_y)) pairs consistent with a fixed
    offset/flip applied to atan2(y, x) — the same convention tracking.py uses
    for heading, so a stationary target's position angle and a flying object's
    velocity angle are calibrated the same way."""
    sign = -1.0 if true_flip else 1.0
    measurements = []
    for world_rad in _WORLD_RAD.values():
        # Invert world = (atan2(y,x) - offset) * sign  ->  atan2(y,x) = world/sign + offset
        math_angle = world_rad * sign + true_offset_rad
        bx, by = (
            math.cos(math_angle),
            math.sin(math_angle),
        )  # atan2(by, bx) == math_angle
        measurements.append((world_rad, (bx, by)))
    return measurements


@pytest.mark.parametrize("true_offset_deg", [0.0, 37.0, -110.0, 179.0])
@pytest.mark.parametrize("true_flip", [False, True])
def test_fit_recovers_known_calibration(true_offset_deg, true_flip):
    true_offset_rad = math.radians(true_offset_deg)
    measurements = _simulate_measurements(true_offset_rad, true_flip)

    offset_rad, flip, rms_deg = fit_calibration(measurements)

    assert flip == true_flip
    assert rms_deg == pytest.approx(0.0, abs=1e-6)
    diff = math.atan2(
        math.sin(offset_rad - true_offset_rad), math.cos(offset_rad - true_offset_rad)
    )
    assert diff == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("true_offset_deg", [0.0, 37.0, -110.0, 179.0])
@pytest.mark.parametrize("true_flip", [False, True])
def test_fit_applies_correctly_to_headings_between_calibration_points(
    true_offset_deg, true_flip
):
    """Regression test for the atan2-argument-order bug.

    The original fit_calibration searched over both atan2(x, y) and
    atan2(y, x) for the *position* angle and picked whichever fit the 4
    calibration dots best. Both orders fit those 4 points perfectly (0.0 RMS)
    because offset/flip can absorb the fixed 90-degree-plus-reflection
    difference between them — so the ambiguous search always silently picked
    atan2(x, y), which does not match tracking.py's atan2(yvel, xvel).

    That bug was invisible if you only checked RMS at the calibration points
    themselves. It only showed up as a 0/+-90/180-degree, heading-dependent
    error once real (non-calibration) fly headings were transformed at
    runtime. This test checks headings *between* the 4 calibration points to
    catch exactly that failure mode.
    """
    true_offset_rad = math.radians(true_offset_deg)
    sign = -1.0 if true_flip else 1.0
    measurements = _simulate_measurements(true_offset_rad, true_flip)

    offset_rad, flip, _ = fit_calibration(measurements)
    fit_sign = -1.0 if flip else 1.0

    for test_deg in range(0, 360, 15):
        raw_heading = _runtime_heading(
            xvel=math.cos(math.radians(test_deg)), yvel=math.sin(math.radians(test_deg))
        )
        world_computed = (raw_heading - offset_rad) * fit_sign
        world_true = (raw_heading - true_offset_rad) * sign

        err = math.degrees(
            math.atan2(
                math.sin(world_computed - world_true),
                math.cos(world_computed - world_true),
            )
        )
        assert err == pytest.approx(0.0, abs=1e-4), (
            f"heading {test_deg} deg: computed world {math.degrees(world_computed):.2f} "
            f"vs true {math.degrees(world_true):.2f} (err {err:.2f} deg)"
        )
