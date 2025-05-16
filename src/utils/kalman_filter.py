"""
Kalman filter implementation for state estimation in 3D space.

This module provides a Kalman filter implementation specialized for tracking
objects in 3D space, predicting their future positions based on current
measurements and a motion model.
"""

import numpy as np
from typing import Tuple, Optional, Dict, Any


class KalmanFilter:
    """
    Kalman filter for tracking objects in 3D space.

    This implementation uses a state vector with 6 dimensions [x, y, z, vx, vy, vz]
    to track both position and velocity in 3D space. It provides methods for
    updating the filter with new measurements and predicting future states.
    """

    def __init__(
        self,
        process_noise: float = 0.01,
        measurement_noise: float = 0.1,
        initial_covariance: float = 1.0,
    ):
        """
        Initialize the Kalman filter with specified parameters.

        Args:
            process_noise: Process noise covariance - how quickly velocity can change
            measurement_noise: Measurement noise covariance - accuracy of position measurements
            initial_covariance: Initial state covariance - uncertainty in initial state
        """
        # State dimension: [x, y, z, vx, vy, vz]
        self.state_dim = 6
        # Measurement dimension: [x, y, z]
        self.measurement_dim = 3

        # Initialize state vector [x, y, z, vx, vy, vz]
        self.x = np.zeros((self.state_dim, 1))

        # Initialize state transition matrix (F) for constant velocity model
        # [ 1 0 0 dt 0  0  ]
        # [ 0 1 0 0  dt 0  ]
        # [ 0 0 1 0  0  dt ]
        # [ 0 0 0 1  0  0  ]
        # [ 0 0 0 0  1  0  ]
        # [ 0 0 0 0  0  1  ]
        self.F = np.eye(self.state_dim)
        # Time step will be updated during prediction
        self.dt = 0.0

        # Initialize measurement matrix (H) to map state to measurements
        # [ 1 0 0 0 0 0 ]
        # [ 0 1 0 0 0 0 ]
        # [ 0 0 1 0 0 0 ]
        self.H = np.zeros((self.measurement_dim, self.state_dim))
        self.H[0, 0] = 1.0  # x
        self.H[1, 1] = 1.0  # y
        self.H[2, 2] = 1.0  # z

        # Process noise covariance (Q)
        self.process_noise = process_noise
        self.Q = np.eye(self.state_dim) * self.process_noise

        # Measurement noise covariance (R)
        self.measurement_noise = measurement_noise
        self.R = np.eye(self.measurement_dim) * self.measurement_noise

        # State covariance matrix (P)
        self.initial_covariance = initial_covariance
        self.P = np.eye(self.state_dim) * self.initial_covariance

        # Initialize timestamp for tracking time between updates
        self.last_timestamp = None

    def init(self, position: Tuple[float, float, float], velocity: Optional[Tuple[float, float, float]] = None,
             timestamp: Optional[float] = None) -> None:
        """
        Initialize the filter state with a position measurement.

        Args:
            position: Initial position (x, y, z)
            velocity: Initial velocity (vx, vy, vz), if available
            timestamp: Timestamp of the initial measurement
        """
        # Initialize state with position
        self.x[0, 0] = position[0]  # x
        self.x[1, 0] = position[1]  # y
        self.x[2, 0] = position[2]  # z

        # Initialize velocity if provided
        if velocity is not None:
            self.x[3, 0] = velocity[0]  # vx
            self.x[4, 0] = velocity[1]  # vy
            self.x[5, 0] = velocity[2]  # vz

        # Store timestamp
        self.last_timestamp = timestamp

    def update(self, position: Tuple[float, float, float], velocity: Optional[Tuple[float, float, float]] = None,
               timestamp: Optional[float] = None) -> None:
        """
        Update the filter with a new measurement.

        Args:
            position: New position measurement (x, y, z)
            velocity: New velocity measurement (vx, vy, vz), if available
            timestamp: Timestamp of the measurement
        """
        # If this is the first measurement, initialize the filter
        if self.last_timestamp is None:
            self.init(position, velocity, timestamp)
            return

        # Update time step if timestamp is provided
        if timestamp is not None and self.last_timestamp is not None:
            self.dt = timestamp - self.last_timestamp
            self.last_timestamp = timestamp

            # Update state transition matrix with new dt
            self.F[0, 3] = self.dt  # x += vx * dt
            self.F[1, 4] = self.dt  # y += vy * dt
            self.F[2, 5] = self.dt  # z += vz * dt

            # Update process noise covariance with dt
            # The process noise increases with time squared for position states
            # and linearly for velocity states
            G = np.array([
                [self.dt**2/2, 0, 0],
                [0, self.dt**2/2, 0],
                [0, 0, self.dt**2/2],
                [self.dt, 0, 0],
                [0, self.dt, 0],
                [0, 0, self.dt]
            ])
            self.Q = G @ np.eye(3) * self.process_noise @ G.T
        
        # Prediction step
        x_pred = self.F @ self.x
        P_pred = self.F @ self.P @ self.F.T + self.Q

        # Create measurement vector
        z = np.array([[position[0]], [position[1]], [position[2]]])

        # Calculate Kalman gain
        S = self.H @ P_pred @ self.H.T + self.R
        K = P_pred @ self.H.T @ np.linalg.inv(S)

        # Update state
        y = z - self.H @ x_pred  # Measurement residual
        self.x = x_pred + K @ y
        self.P = (np.eye(self.state_dim) - K @ self.H) @ P_pred

        # If velocity measurement is provided, directly update those states
        if velocity is not None:
            self.x[3, 0] = velocity[0]  # vx
            self.x[4, 0] = velocity[1]  # vy
            self.x[5, 0] = velocity[2]  # vz

    def predict(self, dt: float = None) -> Tuple[float, float, float]:
        """
        Predict the state at a future time.

        Args:
            dt: Time in the future to predict (seconds). If None, uses the last dt.

        Returns:
            Predicted position (x, y, z)
        """
        if dt is None:
            dt = self.dt

        # Create prediction matrix with specified dt
        F_pred = np.eye(self.state_dim)
        F_pred[0, 3] = dt  # x += vx * dt
        F_pred[1, 4] = dt  # y += vy * dt
        F_pred[2, 5] = dt  # z += vz * dt

        # Predict future state
        x_future = F_pred @ self.x

        # Return predicted position
        return (float(x_future[0, 0]), float(x_future[1, 0]), float(x_future[2, 0]))

    def get_state(self) -> Dict[str, Any]:
        """
        Get the current state estimate.

        Returns:
            Dictionary with position and velocity components
        """
        return {
            "x": float(self.x[0, 0]),
            "y": float(self.x[1, 0]),
            "z": float(self.x[2, 0]),
            "vx": float(self.x[3, 0]),
            "vy": float(self.x[4, 0]),
            "vz": float(self.x[5, 0]),
            "covariance": self.P.copy()
        }