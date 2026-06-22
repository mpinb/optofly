"""
1-D Kalman filter for lens focus tracking.

State: [z, vz] — vertical position and velocity only.
The liquid lens only needs z; tracking x and y in the same filter
was dead weight (the states are fully decoupled in DWNA anyway).
"""

import numpy as np
from typing import Optional


class KalmanFilter:
    """
    2-state Kalman filter: [z, vz].

    Fuses BRAID position (z) and velocity (zvel) measurements with a
    constant-velocity (DWNA) motion model to predict z ahead by an
    arbitrary dt for lens focus compensation.
    """

    def __init__(
        self,
        process_noise: float = 0.01,
        measurement_noise: float = 0.1,
        initial_covariance: float = 1.0,
        velocity_noise: float = 1.0,
    ):
        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.initial_covariance = initial_covariance
        self.velocity_noise = velocity_noise

        # State [z, vz], covariance 2×2
        self.x = np.zeros((2, 1))
        self.P = np.eye(2) * initial_covariance

        # Measurement matrices (1×2)
        self.H_pos = np.array([[1.0, 0.0]])  # observes z
        self.H_vel = np.array([[0.0, 1.0]])  # observes vz

        # Measurement noise (1×1)
        self.R_pos = np.array([[measurement_noise]])
        self.R_vel = np.array([[velocity_noise]])

        self.dt = 0.0
        self.last_timestamp: Optional[float] = None

        # Pre-allocated temporaries reused every update() call
        self._F = np.eye(2)
        self._Q = np.zeros((2, 2))
        self._z_meas = np.zeros((1, 1))
        self._vz_meas = np.zeros((1, 1))

    def init(
        self,
        z: float,
        vz: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> None:
        self.x[0, 0] = z
        self.x[1, 0] = vz
        self.P = np.eye(2) * self.initial_covariance
        self.last_timestamp = timestamp

    def update(
        self,
        z: float,
        vz: Optional[float] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        if self.last_timestamp is None:
            self.init(z, vz if vz is not None else 0.0, timestamp)
            return

        if timestamp is not None:
            self.dt = timestamp - self.last_timestamp
            self.last_timestamp = timestamp

        dt = self.dt
        if dt <= 0:
            return

        # Fill pre-allocated F in-place: [[1, dt], [0, 1]]
        self._F[0, 1] = dt

        # DWNA process noise: Q = process_noise * G G^T, computed element-wise
        g0 = dt * dt / 2.0
        pn = self.process_noise
        self._Q[0, 0] = pn * g0 * g0
        self._Q[0, 1] = pn * g0 * dt
        self._Q[1, 0] = self._Q[0, 1]
        self._Q[1, 1] = pn * dt * dt

        self._z_meas[0, 0] = z
        self.x, self.P = _kalman_update(
            self.x, self.P, self._z_meas, self._F, self.H_pos, self._Q, self.R_pos
        )

        if vz is not None:
            self._vz_meas[0, 0] = vz
            self.x, self.P = _kalman_measurement_update(
                self.x, self.P, self._vz_meas, self.H_vel, self.R_vel
            )

    def predict(self, dt: float) -> float:
        """Return predicted z position `dt` seconds ahead."""
        F_pred = np.array([[1.0, dt], [0.0, 1.0]])
        x_future = F_pred @ self.x
        return float(x_future[0, 0])

    def get_state(self):
        return {
            "z": float(self.x[0, 0]),
            "vz": float(self.x[1, 0]),
            "covariance": self.P.copy(),
        }


def _kalman_update(
    x: np.ndarray,
    P: np.ndarray,
    z: np.ndarray,
    F: np.ndarray,
    H: np.ndarray,
    Q: np.ndarray,
    R: np.ndarray,
) -> tuple:
    """Predict + measurement update, Joseph-form covariance."""
    x_pred = F @ x
    P_pred = F @ P @ F.T + Q

    S_inv = 1.0 / (H @ P_pred @ H.T + R)[0, 0]
    K = P_pred @ H.T * S_inv

    x_new = x_pred + K @ (z - H @ x_pred)

    IKH = np.eye(x.shape[0]) - K @ H
    P_new = IKH @ P_pred @ IKH.T + K @ R @ K.T
    return x_new, P_new


def _kalman_measurement_update(
    x: np.ndarray,
    P: np.ndarray,
    z: np.ndarray,
    H: np.ndarray,
    R: np.ndarray,
) -> tuple:
    """Measurement-only update (no prediction), Joseph-form covariance."""
    S_inv = 1.0 / (H @ P @ H.T + R)[0, 0]
    K = P @ H.T * S_inv

    x_new = x + K @ (z - H @ x)

    IKH = np.eye(x.shape[0]) - K @ H
    P_new = IKH @ P @ IKH.T + K @ R @ K.T
    return x_new, P_new
