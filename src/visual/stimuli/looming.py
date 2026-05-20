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

    Raises:
        ValueError: If final_size_deg is not strictly between 0 and 180.
    """
    if final_size_deg <= 0 or final_size_deg >= 180:
        raise ValueError(
            f"final_size_deg must be strictly between 0 and 180, got {final_size_deg}"
        )
    half_final_rad = math.radians(final_size_deg / 2.0)
    offset_ms = lv_ratio_ms / math.tan(half_final_rad)
    term = lv_ratio_ms / (t_ms + offset_ms)
    return final_size_deg - 2.0 * math.degrees(math.atan(term))


def compute_exponential_size(
    t_ms: float, duration_ms: float, initial_deg: float, final_deg: float
) -> float:
    """Angular size at time t for an exponential expansion.

    Follows θ(t) = θ₀ · exp(k·t) where k is chosen so that θ(duration) = final.

    Args:
        t_ms: Elapsed time since expansion start (ms)
        duration_ms: Total expansion duration (ms)
        initial_deg: Starting angular diameter (degrees)
        final_deg: Ending angular diameter (degrees)

    Returns:
        Angular diameter in degrees

    Raises:
        ValueError: If initial_deg <= 0 or final_deg <= 0.
    """
    if initial_deg <= 0 or final_deg <= 0:
        raise ValueError(
            f"initial_deg and final_deg must be > 0, got {initial_deg}, {final_deg}"
        )
    if duration_ms <= 0:
        return final_deg
    if t_ms <= 0:
        return initial_deg
    k = math.log(final_deg / initial_deg) / duration_ms
    t_clamped = min(t_ms, duration_ms)
    return initial_deg * math.exp(k * t_clamped)


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

    def __init__(self, positions: list, seed: int = 42):
        self._positions = list(positions)
        self._rng = random.Random(seed)
        self._pool: list = []

    def next(self) -> float:
        if not self._pool:
            self._pool = list(self._positions)
            self._rng.shuffle(self._pool)
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
        self._lv_ratio_ms: float = cfg.get("lv_ratio_ms", 40.0)
        self._color: tuple = tuple(cfg.get("color", [0, 0, 0]))
        self._sham_prob: float = cfg.get("sham_probability", 0.0)
        self._expansion_type: str = cfg.get("expansion_type", "lv_ratio")
        if self._expansion_type not in ("lv_ratio", "linear", "exponential"):
            raise ValueError(
                f"Unknown expansion_type '{self._expansion_type}'. "
                "Valid values: 'lv_ratio', 'exponential', 'linear'"
            )

        self._rng_seed: int = cfg.get("seed", 42)
        self._rng = random.Random(self._rng_seed)

        self._balancer = PositionBalancer(
            cfg.get("positions_deg", [0]),
            seed=self._rng_seed + 1,
        )
        self._state = _State.IDLE
        self._disk = None
        self._elapsed_ms: float = 0.0

        # A flat billboard plane at depth D is occluded by the cylinder wall
        # (radius R) for pixels beyond arccos(D/R) from the disk center.
        # We need D < R * cos(final_edge_angle) so the full disk stays in front.
        max_edge_rad = math.radians(self._final_deg / 2.0)
        self._disk_distance_cm = (
            self.scene.viewing_distance_cm * math.cos(max_edge_rad) * 0.95
        )

    def on_trigger(self, heading_deg: float, trigger_data: dict) -> dict | None:
        if self._state != _State.IDLE:
            return None
        base = {
            "looming_expansion_type": self._expansion_type,
            "looming_initial_deg": self._initial_deg,
            "looming_final_deg": self._final_deg,
            "looming_duration_ms": self._duration_ms,
            "looming_lv_ratio_ms": self._lv_ratio_ms,
        }
        if self._rng.random() < self._sham_prob:
            return {**base, "looming_sham": True, "looming_stimulus_heading_deg": None, "looming_offset_deg": None}

        offset_deg = self._balancer.next()
        self._stimulus_heading = heading_deg + offset_deg
        self._elapsed_ms = 0.0
        self._state = _State.EXPANDING

        self._disk = self.add_disk(
            self._stimulus_heading,
            self._initial_deg,
            color=self._color,
            distance_cm=self._disk_distance_cm,
        )
        return {**base, "looming_sham": False, "looming_stimulus_heading_deg": self._stimulus_heading, "looming_offset_deg": offset_deg}

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
        if self._expansion_type == "exponential":
            return compute_exponential_size(
                t_ms, self._duration_ms, self._initial_deg, self._final_deg
            )
        return compute_linear_size(
            t_ms, self._duration_ms, self._initial_deg, self._final_deg
        )
