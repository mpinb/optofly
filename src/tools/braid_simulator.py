#!/usr/bin/env python3
"""Simulate BRAID tracking messages for testing OptoFly processes.

This utility publishes synthetic tracking data to the configured ZMQ BRAID topic,
generating objects that traverse the arena and cross the trigger zone. A new
object is spawned every ``spawn_interval`` seconds, flies from one corner of the
tracking volume to the opposite corner, and introduces small heading jitter to
exercise the TriggerHandler's heading cone configuration.

Run with ``python -m src.tools.braid_simulator`` (or execute the file directly)
after ensuring no other process is bound to the BRAID publisher port.
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
import zmq

from src.utils.config import ConfigBase, TriggerHandlerConfig, ZMQConfig


def clamp(value: float, lower: float, upper: float) -> float:
    """Clamp ``value`` into the inclusive range ``[lower, upper]``."""

    return max(lower, min(upper, value))


@dataclass
class SimulatedFly:
    """Simple state container describing one synthetic trajectory."""

    obj_id: int
    start_time: float
    duration: float
    start_pos: np.ndarray
    end_pos: np.ndarray
    z_bounds: Tuple[float, float]
    noise_std: float
    frame: int = 0
    birth_position: np.ndarray = field(init=False)
    birth_velocity: np.ndarray = field(init=False)
    previous_position: Optional[np.ndarray] = field(default=None, init=False)
    last_position: Optional[np.ndarray] = field(default=None, init=False)
    crossed_midline: bool = field(default=False, init=False)
    entered_trigger_zone: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        # Draw a noisy measurement for the birth event so it doesn't align
        # perfectly with the underlying path.
        self.birth_position = self._apply_position_noise(self.start_pos)

        base_velocity = (self.end_pos - self.start_pos) / max(self.duration, 1e-6)
        self.birth_velocity = self._apply_velocity_noise(base_velocity)

    def current_state(
        self, timestamp: float
    ) -> Tuple[np.ndarray, np.ndarray, int, float]:
        """Return noisy position and velocity for the requested ``timestamp``."""

        progress = clamp((timestamp - self.start_time) / self.duration, 0.0, 1.0)
        base_position = self.start_pos + (self.end_pos - self.start_pos) * progress
        position = self._apply_position_noise(base_position)

        base_velocity = (self.end_pos - self.start_pos) / max(self.duration, 1e-6)
        velocity = self._apply_velocity_noise(base_velocity)

        frame = self.frame
        self.previous_position = getattr(self, "last_position", None)
        self.last_position = position
        self.frame += 1

        return position, velocity, frame, progress

    # Internal helpers -------------------------------------------------

    def _apply_position_noise(self, position: np.ndarray) -> np.ndarray:
        noisy = np.array(position, dtype=float) + np.random.normal(
            scale=self.noise_std, size=3
        )
        noisy[2] = clamp(noisy[2], *self.z_bounds)
        return noisy

    def _apply_velocity_noise(self, velocity: np.ndarray) -> np.ndarray:
        return np.array(velocity, dtype=float) + np.random.normal(
            scale=self.noise_std / max(self.duration, 1e-6), size=3
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish synthetic BRAID tracking data for OptoFly debugging",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="configs/config.toml",
        help="Path to configuration file (default: configs/config.toml)",
    )
    parser.add_argument(
        "--spawn-interval",
        type=float,
        default=5.0,
        help="Seconds between spawning new simulated objects",
    )
    parser.add_argument(
        "--flight-duration",
        type=float,
        default=6.0,
        help="Seconds each object remains in view",
    )
    parser.add_argument(
        "--update-rate",
        type=float,
        default=50.0,
        help="Updates per second emitted for each object",
    )
    parser.add_argument(
        "--noise-std",
        type=float,
        default=0.0015,
        help="Gaussian noise (meters) added to position/velocity measurements",
    )
    parser.add_argument(
        "--jitter",
        type=float,
        default=0.02,
        help="Heading jitter (meters) applied to corner start/end positions",
    )
    parser.add_argument(
        "--runtime",
        type=float,
        default=None,
        help="Optional runtime limit in seconds (default: run until interrupted)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible trajectories",
    )
    return parser.parse_args()


def load_environment(
    config_path: str,
) -> Tuple[ZMQConfig, Sequence[Tuple[float, float]], TriggerHandlerConfig]:
    """Load ZMQ settings, arena corners, and trigger configuration."""

    zmq_config = ZMQConfig(config_path)

    config_root = ConfigBase(config_path)._load_config()
    camera_config = config_root.get("camera", {})
    fov = camera_config.get(
        "FOV", {"x_min": -0.1, "x_max": 0.1, "y_min": -0.1, "y_max": 0.1}
    )
    x_min = float(fov.get("x_min", -0.1))
    x_max = float(fov.get("x_max", 0.1))
    y_min = float(fov.get("y_min", -0.1))
    y_max = float(fov.get("y_max", 0.1))

    corners = [
        (x_min, y_min),
        (x_min, y_max),
        (x_max, y_min),
        (x_max, y_max),
    ]

    trigger_config = TriggerHandlerConfig(config_path)

    return zmq_config, corners, trigger_config


def create_fly(
    obj_id: int,
    start_time: float,
    corners: Sequence[Tuple[float, float]],
    z_bounds: Tuple[float, float],
    duration: float,
    jitter: float,
    noise_std: float,
) -> SimulatedFly:
    """Instantiate one synthetic fly with jittered path across the arena."""

    start_corner = random.choice(corners)
    # Opposite corner ensures a midline crossing; jitter adds heading variety.
    end_corner = (-start_corner[0], -start_corner[1])

    start_pos = np.array(
        [
            start_corner[0] + random.uniform(-jitter, jitter),
            start_corner[1] + random.uniform(-jitter, jitter),
            np.mean(z_bounds) + random.uniform(-jitter, jitter) * 0.2,
        ]
    )

    end_pos = np.array(
        [
            end_corner[0] + random.uniform(-jitter, jitter),
            end_corner[1] + random.uniform(-jitter, jitter),
            np.mean(z_bounds) + random.uniform(-jitter, jitter) * 0.2,
        ]
    )

    # Ensure we maintain a sign flip across both axes so the path traverses the centre.
    end_pos[0] = -abs(end_pos[0]) if start_pos[0] > 0 else abs(end_pos[0])
    end_pos[1] = -abs(end_pos[1]) if start_pos[1] > 0 else abs(end_pos[1])

    # Keep positions inside the field of view bounds.
    start_pos[0] = clamp(
        start_pos[0], min(c[0] for c in corners), max(c[0] for c in corners)
    )
    start_pos[1] = clamp(
        start_pos[1], min(c[1] for c in corners), max(c[1] for c in corners)
    )
    end_pos[0] = clamp(
        end_pos[0], min(c[0] for c in corners), max(c[0] for c in corners)
    )
    end_pos[1] = clamp(
        end_pos[1], min(c[1] for c in corners), max(c[1] for c in corners)
    )

    start_pos[2] = clamp(start_pos[2], *z_bounds)
    end_pos[2] = clamp(end_pos[2], *z_bounds)

    return SimulatedFly(
        obj_id=obj_id,
        start_time=start_time,
        duration=duration,
        start_pos=start_pos,
        end_pos=end_pos,
        z_bounds=z_bounds,
        noise_std=noise_std,
    )


def publish(socket: zmq.Socket, topic: str, payload: dict) -> None:
    """Publish a JSON payload on the configured topic."""

    message = json.dumps(payload)
    socket.send_string(f"{topic} {message}")


def run_simulation(args: argparse.Namespace) -> None:
    zmq_config, corners, trigger_config = load_environment(args.config)
    z_bounds = trigger_config.z_lim
    trigger_radius = trigger_config.radius

    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)

    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    bind_address = zmq_config.get_publisher_address(zmq_config.braid_port)
    publisher.bind(bind_address)
    time.sleep(0.2)  # Allow subscribers time to connect

    topic = zmq_config.braid_topic
    active: List[SimulatedFly] = []
    next_spawn = time.time()
    next_obj_id = 1

    stop_requested = False

    def _handle_signal(signum, frame):  # noqa: D401 - simple signal handler
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    start_time = time.time()
    update_period = 1.0 / max(args.update_rate, 1.0)

    try:
        while not stop_requested:
            now = time.time()

            if args.runtime is not None and now - start_time > args.runtime:
                break

            if now >= next_spawn:
                fly = create_fly(
                    obj_id=next_obj_id,
                    start_time=now,
                    corners=corners,
                    z_bounds=z_bounds,
                    duration=args.flight_duration,
                    jitter=args.jitter,
                    noise_std=args.noise_std,
                )

                birth_payload = {
                    "Birth": {
                        "obj_id": fly.obj_id,
                        "timestamp": now,
                        "frame": 0,
                        "x": float(fly.birth_position[0]),
                        "y": float(fly.birth_position[1]),
                        "z": float(fly.birth_position[2]),
                        "xvel": float(fly.birth_velocity[0]),
                        "yvel": float(fly.birth_velocity[1]),
                        "zvel": float(fly.birth_velocity[2]),
                    }
                }
                publish(publisher, topic, birth_payload)
                print(
                    f"[{now:.3f}] Spawned obj {fly.obj_id} "
                    f"from ({fly.birth_position[0]:.3f}, {fly.birth_position[1]:.3f}, {fly.birth_position[2]:.3f})"
                )

                active.append(fly)
                next_obj_id += 1
                next_spawn = now + args.spawn_interval

            for fly in list(active):
                position, velocity, frame, progress = fly.current_state(now)

                # Midline crossing detection (x coordinate sign change)
                prev = fly.previous_position
                if (
                    not fly.crossed_midline
                    and prev is not None
                    and prev[0] * position[0] <= 0
                    and abs(position[0] - prev[0]) > 1e-4
                ):
                    fly.crossed_midline = True
                    print(
                        f"[{now:.3f}] Obj {fly.obj_id} crossed midline at x={position[0]:.3f}"
                    )

                # Trigger zone entry detection
                distance_xy = float(np.linalg.norm(position[:2]))
                in_trigger = (
                    distance_xy <= trigger_radius
                    and z_bounds[0] <= position[2] <= z_bounds[1]
                )
                if in_trigger and not fly.entered_trigger_zone:
                    fly.entered_trigger_zone = True
                    print(
                        f"[{now:.3f}] Obj {fly.obj_id} entered trigger zone "
                        f"(r={distance_xy:.3f}, z={position[2]:.3f})"
                    )

                update_payload = {
                    "Update": {
                        "obj_id": fly.obj_id,
                        "timestamp": now,
                        "frame": frame,
                        "x": float(position[0]),
                        "y": float(position[1]),
                        "z": float(position[2]),
                        "xvel": float(velocity[0]),
                        "yvel": float(velocity[1]),
                        "zvel": float(velocity[2]),
                    }
                }
                publish(publisher, topic, update_payload)

                if progress >= 1.0:
                    publish(publisher, topic, {"Death": fly.obj_id})
                    print(f"[{now:.3f}] Obj {fly.obj_id} left FOV")
                    active.remove(fly)

            time.sleep(update_period)
    finally:
        for fly in active:
            publish(publisher, topic, {"Death": fly.obj_id})
            print(f"[{time.time():.3f}] Obj {fly.obj_id} cleaned up on shutdown")

        publisher.close(0)
        context.term()


def main() -> None:
    args = parse_args()
    run_simulation(args)


if __name__ == "__main__":
    main()
