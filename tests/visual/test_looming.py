import math
from unittest.mock import MagicMock

from src.visual.stimuli.looming import (
    compute_lv_ratio_size,
    compute_exponential_size,
    compute_linear_size,
    PositionBalancer,
    LoomingStimulus,
)


def _make_looming(config: dict) -> LoomingStimulus:
    stim = object.__new__(LoomingStimulus)
    stim.scene = MagicMock(viewing_distance_cm=25.0)
    stim.config = config
    stim.setup()
    return stim


def test_on_trigger_sham_draw_reports_sham_true_and_no_position():
    stim = _make_looming({"sham_probability": 1.0})
    result = stim.on_trigger(heading_deg=0.0, trigger_data={})
    assert result["sham"] is True
    assert result["looming_sham"] is True


def test_on_trigger_real_draw_reports_sham_false():
    stim = _make_looming({"sham_probability": 0.0})
    stim.add_disk = MagicMock(return_value=MagicMock())
    result = stim.on_trigger(heading_deg=0.0, trigger_data={})
    assert result["sham"] is False
    assert result["looming_sham"] is False


def test_exponential_start():
    size = compute_exponential_size(
        t_ms=0, duration_ms=500, initial_deg=5.0, final_deg=72.0
    )
    assert abs(size - 5.0) < 1e-6


def test_exponential_end():
    size = compute_exponential_size(
        t_ms=500, duration_ms=500, initial_deg=5.0, final_deg=72.0
    )
    assert abs(size - 72.0) < 1e-6


def test_exponential_monotonically_increasing():
    sizes = [compute_exponential_size(t, 500, 5.0, 72.0) for t in range(0, 500, 10)]
    assert all(sizes[i] <= sizes[i + 1] for i in range(len(sizes) - 1))


def test_exponential_midpoint():
    # At t = duration/2, size = initial * sqrt(final/initial) = sqrt(initial*final)
    size = compute_exponential_size(
        t_ms=250, duration_ms=500, initial_deg=5.0, final_deg=72.0
    )
    expected = math.sqrt(5.0 * 72.0)
    assert abs(size - expected) < 1e-6


def test_exponential_zero_duration_returns_final():
    size = compute_exponential_size(
        t_ms=0, duration_ms=0, initial_deg=5.0, final_deg=72.0
    )
    assert abs(size - 72.0) < 1e-6


def test_lv_ratio_starts_small():
    size = compute_lv_ratio_size(t_ms=0, lv_ratio_ms=40.0, final_size_deg=72.0)
    assert size < 5.0


def test_lv_ratio_approaches_final_near_end():
    size = compute_lv_ratio_size(t_ms=499.9, lv_ratio_ms=40.0, final_size_deg=72.0)
    assert size > 60.0
    assert size <= 72.0


def test_lv_ratio_monotonically_increasing():
    sizes = [compute_lv_ratio_size(t, 40.0, 72.0) for t in range(0, 500, 10)]
    assert all(sizes[i] <= sizes[i + 1] for i in range(len(sizes) - 1))


def test_linear_start():
    size = compute_linear_size(t_ms=0, duration_ms=500, initial_deg=5.0, final_deg=72.0)
    assert abs(size - 5.0) < 1e-6


def test_linear_end():
    size = compute_linear_size(
        t_ms=500, duration_ms=500, initial_deg=5.0, final_deg=72.0
    )
    assert abs(size - 72.0) < 1e-6


def test_linear_midpoint():
    size = compute_linear_size(
        t_ms=250, duration_ms=500, initial_deg=5.0, final_deg=72.0
    )
    assert abs(size - (5.0 + 72.0) / 2.0) < 1e-6


def test_position_balancer_exhausts_evenly():
    positions = [-90, 0, 90]
    balancer = PositionBalancer(positions)
    counts = {p: 0 for p in positions}
    for _ in range(30):
        p = balancer.next()
        counts[p] += 1
    assert all(v == 10 for v in counts.values())


def test_position_balancer_cycles_after_pool_exhausted():
    balancer = PositionBalancer([0, 1])
    drawn = [balancer.next() for _ in range(6)]
    assert set(drawn) == {0, 1}
