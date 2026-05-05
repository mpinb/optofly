import math
import pytest
from src.visual.base import angular_to_world_pos, angular_size_to_radius


def test_north_heading():
    x, y, z = angular_to_world_pos(0.0, 0.0, 25.0)
    assert abs(x) < 1e-9
    assert abs(y - 25.0) < 1e-9
    assert abs(z) < 1e-9


def test_east_heading():
    x, y, z = angular_to_world_pos(90.0, 0.0, 25.0)
    assert abs(x - 25.0) < 1e-9
    assert abs(y) < 1e-9
    assert abs(z) < 1e-9


def test_south_heading():
    x, y, z = angular_to_world_pos(180.0, 0.0, 25.0)
    assert abs(x) < 1e-9
    assert abs(y + 25.0) < 1e-9
    assert abs(z) < 1e-9


def test_west_heading():
    x, y, z = angular_to_world_pos(270.0, 0.0, 25.0)
    assert abs(x + 25.0) < 1e-9
    assert abs(y) < 1e-9
    assert abs(z) < 1e-9


def test_elevation_up():
    x, y, z = angular_to_world_pos(0.0, 45.0, 25.0)
    assert abs(z - 25.0 * math.sin(math.radians(45.0))) < 1e-6


def test_distance_preserved():
    x, y, z = angular_to_world_pos(37.0, 12.0, 25.0)
    assert abs(math.sqrt(x**2 + y**2 + z**2) - 25.0) < 1e-6


def test_angular_size_to_radius_72_deg():
    R = angular_size_to_radius(72.0, 25.0)
    expected = math.tan(math.radians(36.0)) * 25.0
    assert abs(R - expected) < 1e-9


def test_angular_size_roundtrip():
    R = angular_size_to_radius(72.0, 25.0)
    recovered = 2.0 * math.degrees(math.atan(R / 25.0))
    assert abs(recovered - 72.0) < 1e-9


def test_angular_size_scales_linearly_with_distance():
    R1 = angular_size_to_radius(10.0, 25.0)
    R2 = angular_size_to_radius(10.0, 50.0)
    assert abs(R2 - 2.0 * R1) < 1e-9


@pytest.mark.parametrize("distance_cm", [0.0, 1.0, 100.0])
def test_angular_size_to_radius_zero_returns_zero(distance_cm):
    R = angular_size_to_radius(0.0, distance_cm)
    assert abs(R) < 1e-9


@pytest.mark.parametrize("elevation_deg", [-45.0, -10.0, 0.0, 10.0, 45.0])
def test_elevation_sign(elevation_deg):
    x, y, z = angular_to_world_pos(0.0, elevation_deg, 25.0)
    # z should match sign of elevation
    if elevation_deg > 0:
        assert z > 0
    elif elevation_deg < 0:
        assert z < 0
    else:
        assert abs(z) < 1e-9


def test_angular_to_world_pos_zero_distance():
    x, y, z = angular_to_world_pos(45.0, 30.0, 0.0)
    assert abs(x) < 1e-9
    assert abs(y) < 1e-9
    assert abs(z) < 1e-9
