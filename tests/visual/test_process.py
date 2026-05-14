import math
from src.visual.process import braid_to_world_heading


def test_no_offset_no_flip():
    result = braid_to_world_heading(math.pi / 2, offset_rad=0.0, flip=False)
    assert abs(result - 90.0) < 1e-6


def test_with_offset():
    # offset=pi/2 means "Braid 90 deg corresponds to North"
    result = braid_to_world_heading(math.pi / 2, offset_rad=math.pi / 2, flip=False)
    assert abs(result - 0.0) < 1e-6


def test_with_flip():
    result = braid_to_world_heading(math.pi / 2, offset_rad=0.0, flip=True)
    assert abs(result - (-90.0)) < 1e-6


def test_offset_and_flip():
    result = braid_to_world_heading(math.pi / 2, offset_rad=math.pi / 4, flip=True)
    expected = -math.degrees(math.pi / 2 - math.pi / 4)
    assert abs(result - expected) < 1e-6
