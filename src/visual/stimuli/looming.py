import math
import random
from enum import Enum, auto

from src.visual.base import BaseStimulus


def compute_lv_ratio_size(
    t_ms: float, lv_ratio_ms: float, final_size_deg: float
) -> float:
    """Angular size at time t for an L/V ratio looming stimulus.

    Models an object approaching at constant velocity. The expansion follows:
        theta(t) = final_size - 2 * arctan(L/V / (t + T))
    where T = (L/V) / tan(final/2) is a time offset ensuring theta(0) ~ 0.

    The L/V ratio controls expansion speed: smaller values = faster expansion.

    Args:
        t_ms: Elapsed time since expansion start (ms)
        lv_ratio_ms: L/V ratio (ms) -- smaller means faster expansion
        final_size_deg: Asymptotic angular diameter in degrees

    Returns:
        Angular diameter in degrees
    """
    half_final_rad = math.radians(final_size_deg / 2.0)
    offset_ms = lv_ratio_ms / math.tan(half_final_rad)
    term = lv_ratio_ms / (t_ms + offset_ms)
    return final_size_deg - 2.0 * math.degrees(math.atan(term))


def compute_linear_size(
    t_ms: float, duration_ms: float, initial_deg: float, final_deg: float
) -> float:
    """Angular size at time t for a linear expansion.

    Args:
        t_ms: Elapsed time since expansion start (ms)
        duration_ms: Total expansion duration (ms)
        initial_deg: Starting angular diameter (degrees)
        final_deg: Ending angular diameter (degrees)

    Returns:
        Angular diameter in degrees
    """
    t_clamped = max(0.0, min(t_ms, duration_ms))
    frac = t_clamped / duration_ms if duration_ms > 0 else 1.0
    return initial_deg + frac * (final_deg - initial_deg)


class PositionBalancer:
    """Returns positions from a list in balanced random order.

    Each position is used exactly once before any is repeated.
    """

    def __init__(self, positions: list):
        self._positions = list(positions)
        self._pool: list = []

    def next(self) -> float:
        if not self._pool:
            self._pool = list(self._positions)
            random.shuffle(self._pool)
        return self._pool.pop()


class _State(Enum):
    IDLE = auto()
    EXPANDING = auto()
    HOLDING = auto()


class LoomingStimulus(BaseStimulus):
    """Billboard disk that expands on ZONE_ENTER and disappears after hold_time_ms.

    Position is chosen from positions_deg (balanced random), offset from the
    fly's world heading. Supports L/V ratio and linear expansion dynamics.
    Sham trials (no stimulus) are supported via sham_probability.
    """

    def setup(self) -> None:
        cfg = self.config
        self._initial_deg: float = cfg.get("initial_size_deg", 5.0)
        self._final_deg: float = cfg.get("final_size_deg", 72.0)
        self._duration_ms: float = cfg.get("expansion_duration_ms", 500)
        self._hold_ms: float = cfg.get("hold_time_ms", 200)
        self._expansion_type: str = cfg.get("expansion_type", "lv_ratio")
        self._lv_ratio_ms: float = cfg.get("lv_ratio_ms", 40.0)
        self._color: tuple = tuple(cfg.get("color", [0, 0, 0]))
        self._sham_prob: float = cfg.get("sham_probability", 0.0)

        self._balancer = PositionBalancer(cfg.get("positions_deg", [0]))
        self._state = _State.IDLE
        self._disk = None
        self._elapsed_ms: float = 0.0

    def on_trigger(self, heading_deg: float, trigger_data: dict) -> None:
        if self._state != _State.IDLE:
            return
        if random.random() < self._sham_prob:
            return

        offset_deg = self._balancer.next()
        self._stimulus_heading = heading_deg + offset_deg
        self._elapsed_ms = 0.0
        self._state = _State.EXPANDING

        self._disk = self.add_disk(
            self._stimulus_heading,
            self._initial_deg,
            color=self._color,
        )

    def update(self, dt: float) -> None:
        if self._state == _State.IDLE or self._disk is None:
            return

        self._elapsed_ms += dt * 1000.0

        if self._state == _State.EXPANDING:
            if self._elapsed_ms >= self._duration_ms:
                self.set_angular_size(self._disk, self._final_deg)
                self._elapsed_ms = 0.0
                self._state = _State.HOLDING
            else:
                size = self._size_at(self._elapsed_ms)
                self.set_angular_size(self._disk, size)

        elif self._state == _State.HOLDING:
            if self._elapsed_ms >= self._hold_ms:
                self.remove_node(self._disk)
                self._disk = None
                self._state = _State.IDLE

    def _size_at(self, t_ms: float) -> float:
        if self._expansion_type == "lv_ratio":
            return compute_lv_ratio_size(t_ms, self._lv_ratio_ms, self._final_deg)
        return compute_linear_size(t_ms, self._duration_ms, self._initial_deg, self._final_deg)
