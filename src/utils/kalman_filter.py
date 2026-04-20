"""
Kalman filter implementation for state estimation in 3D space.

This module provides a Kalman filter implementation specialized for tracking
objects in 3D space, predicting their future positions based on current
measurements and a motion model. This implementation uses Numba for accelerated
numerical computations.
"""

import numpy as np
from typing import Tuple, Optional, Dict, Any
import numba as nb


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
        velocity_noise: float = 1.0,
    ):
        """
        Initialize the Kalman filter with specified parameters.

        Args:
            process_noise: Process noise covariance - how quickly velocity can change
            measurement_noise: Measurement noise covariance - accuracy of position measurements
            initial_covariance: Initial state covariance - uncertainty in initial state
            velocity_noise: Measurement noise for velocity observations (Braid xvel/yvel/zvel).
                            When velocity is provided it is fused as a proper measurement rather
                            than overwriting the state, so P stays consistent.
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

        # Position measurement matrix H_pos: maps state → [x, y, z]
        # [ 1 0 0 0 0 0 ]
        # [ 0 1 0 0 0 0 ]
        # [ 0 0 1 0 0 0 ]
        self.H = np.zeros((self.measurement_dim, self.state_dim))
        self.H[0, 0] = 1.0  # x
        self.H[1, 1] = 1.0  # y
        self.H[2, 2] = 1.0  # z

        # Velocity measurement matrix H_vel: maps state → [vx, vy, vz]
        # [ 0 0 0 1 0 0 ]
        # [ 0 0 0 0 1 0 ]
        # [ 0 0 0 0 0 1 ]
        self.H_vel = np.zeros((self.measurement_dim, self.state_dim))
        self.H_vel[0, 3] = 1.0  # vx
        self.H_vel[1, 4] = 1.0  # vy
        self.H_vel[2, 5] = 1.0  # vz

        # Process noise covariance (Q)
        self.process_noise = process_noise
        self.Q = np.eye(self.state_dim) * self.process_noise

        # Measurement noise covariance (R) — position
        self.measurement_noise = measurement_noise
        self.R = np.eye(self.measurement_dim) * self.measurement_noise

        # Measurement noise covariance for velocity (R_vel)
        self.velocity_noise = velocity_noise
        self.R_vel = np.eye(self.measurement_dim) * self.velocity_noise

        # State covariance matrix (P)
        self.initial_covariance = initial_covariance
        self.P = np.eye(self.state_dim) * self.initial_covariance

        # Initialize timestamp for tracking time between updates
        self.last_timestamp = None

    def init(
        self,
        position: Tuple[float, float, float],
        velocity: Optional[Tuple[float, float, float]] = None,
        timestamp: Optional[float] = None,
    ) -> None:
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

    def update(
        self,
        position: Tuple[float, float, float],
        velocity: Optional[Tuple[float, float, float]] = None,
        timestamp: Optional[float] = None,
    ) -> None:
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
            G = np.array(
                [
                    [self.dt**2 / 2, 0, 0],
                    [0, self.dt**2 / 2, 0],
                    [0, 0, self.dt**2 / 2],
                    [self.dt, 0, 0],
                    [0, self.dt, 0],
                    [0, 0, self.dt],
                ]
            )
            self.Q = calculate_process_noise(G, np.eye(3), self.process_noise)

        # Create measurement vector
        z = np.array([[position[0]], [position[1]], [position[2]]])

        # Prediction + position measurement update (Joseph form covariance)
        self.x, self.P = kalman_update(
            self.x, self.P, z, self.F, self.H, self.Q, self.R, self.state_dim
        )

        # If velocity is provided, fuse it as a second sequential measurement update.
        # This keeps P consistent with x — unlike a direct overwrite which would leave
        # P reflecting uncertainty that no longer exists in the velocity states.
        if velocity is not None:
            z_vel = np.array([[velocity[0]], [velocity[1]], [velocity[2]]])
            self.x, self.P = kalman_measurement_update(
                self.x, self.P, z_vel, self.H_vel, self.R_vel, self.state_dim
            )

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

        # Use the accelerated prediction function
        x_future = kalman_predict(self.x, F_pred)

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
            "covariance": self.P.copy(),
        }


# Numba-accelerated functions for the Kalman filter


@nb.njit
def calculate_process_noise(
    G: np.ndarray, eye3: np.ndarray, process_noise: float
) -> np.ndarray:
    """
    Calculate the process noise covariance matrix Q.

    Args:
        G: Process noise coupling matrix
        eye3: 3x3 identity matrix
        process_noise: Process noise parameter

    Returns:
        Process noise covariance matrix Q
    """
    return G @ (eye3 * process_noise) @ G.T


@nb.njit
def kalman_predict(x: np.ndarray, F: np.ndarray) -> np.ndarray:
    """
    Accelerated Kalman filter prediction step.

    Args:
        x: Current state vector
        F: State transition matrix

    Returns:
        Predicted state vector
    """
    return F @ x


@nb.njit
def kalman_update(
    x: np.ndarray,
    P: np.ndarray,
    z: np.ndarray,
    F: np.ndarray,
    H: np.ndarray,
    Q: np.ndarray,
    R: np.ndarray,
    state_dim: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Accelerated Kalman filter predict + measurement update step.

    Uses the Joseph form for the covariance update:
        P = (I - KH) P_pred (I - KH)' + K R K'
    which is numerically stable regardless of Kalman gain accuracy,
    whereas the simpler (I - KH) P_pred can become non-symmetric over time.

    Args:
        x: Current state vector
        P: Current state covariance matrix
        z: Measurement vector
        F: State transition matrix
        H: Measurement matrix
        Q: Process noise covariance matrix
        R: Measurement noise covariance matrix
        state_dim: Dimension of the state vector

    Returns:
        Updated state vector and state covariance matrix
    """
    # Prediction step
    x_pred = F @ x
    P_pred = F @ P @ F.T + Q

    # Innovation covariance and Kalman gain
    S = H @ P_pred @ H.T + R
    K = P_pred @ H.T @ np.linalg.inv(S)

    # State update
    x_new = x_pred + K @ (z - H @ x_pred)

    # Covariance update — Joseph form: (I-KH) P (I-KH)' + K R K'
    IKH = np.eye(state_dim) - K @ H
    P_new = IKH @ P_pred @ IKH.T + K @ R @ K.T

    return x_new, P_new


@nb.njit
def kalman_measurement_update(
    x: np.ndarray,
    P: np.ndarray,
    z: np.ndarray,
    H: np.ndarray,
    R: np.ndarray,
    state_dim: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Measurement-only update step (no prediction).

    Used for sequential fusion of independent measurements within the same
    time step — e.g. fusing velocity observations after a position update.
    Also uses the Joseph form for numerical stability.

    Args:
        x: Current state vector
        P: Current state covariance matrix
        z: Measurement vector
        H: Measurement matrix
        R: Measurement noise covariance matrix
        state_dim: Dimension of the state vector

    Returns:
        Updated state vector and state covariance matrix
    """
    S = H @ P @ H.T + R
    K = P @ H.T @ np.linalg.inv(S)

    x_new = x + K @ (z - H @ x)

    IKH = np.eye(state_dim) - K @ H
    P_new = IKH @ P @ IKH.T + K @ R @ K.T

    return x_new, P_new


if __name__ == "__main__":
    import time
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 - required for 3D projection

    print("Testing Kalman Filter implementation...")

    # Create a synthetic 3D trajectory with some noise
    def generate_noisy_trajectory():
        # Time points
        t = np.linspace(0, 10, 100)

        # True positions (circular motion in xy-plane with varying z)
        radius = 5
        x_true = radius * np.cos(t)
        y_true = radius * np.sin(t)
        z_true = 2 * np.sin(t / 2) + 10

        # Add measurement noise
        noise_level = 0.3
        x_measured = x_true + np.random.normal(0, noise_level, len(t))
        y_measured = y_true + np.random.normal(0, noise_level, len(t))
        z_measured = z_true + np.random.normal(0, noise_level, len(t))

        # Calculate velocities (with noise)
        dt = t[1] - t[0]
        vx = np.gradient(x_true, dt) + np.random.normal(0, 0.1, len(t))
        vy = np.gradient(y_true, dt) + np.random.normal(0, 0.1, len(t))
        vz = np.gradient(z_true, dt) + np.random.normal(0, 0.1, len(t))

        return (
            t,
            (x_true, y_true, z_true),
            (x_measured, y_measured, z_measured),
            (vx, vy, vz),
        )

    # Generate test data
    times, true_positions, measured_positions, velocities = generate_noisy_trajectory()

    # Initialize Kalman Filter with different process noise values
    kf = KalmanFilter(process_noise=0.1, measurement_noise=0.3)

    # Store filtered positions and predictions
    filtered_positions = []
    predicted_positions = []

    # Initialize timing variables
    update_times = []
    predict_times = []

    # Process measurements one by one
    print("Processing 100 measurements with Kalman filter...")
    for i in range(len(times)):
        pos = (
            measured_positions[0][i],
            measured_positions[1][i],
            measured_positions[2][i],
        )
        vel = (velocities[0][i], velocities[1][i], velocities[2][i])

        # Time the update operation
        t0 = time.time()
        kf.update(pos, vel, times[i])
        t1 = time.time()
        update_times.append(t1 - t0)

        # Store filtered state
        state = kf.get_state()
        filtered_positions.append((state["x"], state["y"], state["z"]))

        # Time the prediction operation
        if i < len(times) - 1:
            prediction_dt = 0.2
            t0 = time.time()
            pred_pos = kf.predict(prediction_dt)
            t1 = time.time()
            predict_times.append(t1 - t0)
            predicted_positions.append(pred_pos)

    # Convert lists to arrays for easier plotting
    filtered_positions = np.array(filtered_positions)
    predicted_positions = np.array(predicted_positions)

    # Print timing statistics
    avg_update_time = np.mean(update_times) * 1000  # Convert to ms
    avg_predict_time = np.mean(predict_times) * 1000  # Convert to ms

    print("\nPerformance Statistics:")
    print(f"Average update time: {avg_update_time:.3f} ms")
    print(f"Average predict time: {avg_predict_time:.3f} ms")
    print(f"Total update time: {sum(update_times) * 1000:.3f} ms")
    print(f"Total predict time: {sum(predict_times) * 1000:.3f} ms")
    print(
        f"Total processing time: {(sum(update_times) + sum(predict_times)) * 1000:.3f} ms"
    )

    # Also run a more demanding benchmark with many iterations
    print("\nRunning benchmark with 10,000 updates...")
    benchmark_times = []
    for _ in range(10000):
        # Random position and velocity
        pos = tuple(np.random.randn(3))
        vel = tuple(np.random.randn(3))

        t0 = time.time()
        kf.update(pos, vel, time.time())
        t1 = time.time()
        benchmark_times.append(t1 - t0)

    avg_benchmark_time = np.mean(benchmark_times) * 1000  # Convert to ms
    print(
        f"Benchmark average update time (10,000 iterations): {avg_benchmark_time:.3f} ms"
    )

    # Create 3D plot
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection="3d")

    # Plot true trajectory
    ax.plot(
        true_positions[0],
        true_positions[1],
        true_positions[2],
        "g-",
        linewidth=2,
        label="True Path",
    )

    # Plot measured positions
    ax.scatter(
        measured_positions[0],
        measured_positions[1],
        measured_positions[2],
        c="r",
        marker="o",
        s=10,
        label="Measurements",
    )

    # Plot filtered positions
    ax.plot(
        filtered_positions[:, 0],
        filtered_positions[:, 1],
        filtered_positions[:, 2],
        "b-",
        linewidth=2,
        label="Filtered Path",
    )

    # Plot some predicted positions (every 5th point)
    indices = range(0, len(predicted_positions), 5)
    for idx in indices:
        ax.scatter(
            predicted_positions[idx, 0],
            predicted_positions[idx, 1],
            predicted_positions[idx, 2],
            c="purple",
            marker="x",
            s=30,
        )

    # Add a legend
    ax.legend()

    # Set labels
    ax.set_xlabel("X Position")
    ax.set_ylabel("Y Position")
    ax.set_zlabel("Z Position")
    ax.set_title("Kalman Filter Performance on 3D Trajectory")

    # Set equal aspect ratio for 3D plot
    ax.set_box_aspect([1, 1, 1])

    # Save the figure
    plt.savefig("kalman_filter_test.png")

    print("Results saved to kalman_filter_test.png")

    # Print some statistics
    true_final = np.array(
        [true_positions[0][-1], true_positions[1][-1], true_positions[2][-1]]
    )
    measured_final = np.array(
        [
            measured_positions[0][-1],
            measured_positions[1][-1],
            measured_positions[2][-1],
        ]
    )
    filtered_final = np.array(
        [
            filtered_positions[-1, 0],
            filtered_positions[-1, 1],
            filtered_positions[-1, 2],
        ]
    )

    print(
        f"Final position error (measured): {np.linalg.norm(true_final - measured_final):.3f}"
    )
    print(
        f"Final position error (filtered): {np.linalg.norm(true_final - filtered_final):.3f}"
    )
