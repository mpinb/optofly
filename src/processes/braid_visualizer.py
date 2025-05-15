#!/usr/bin/env python3
"""
BraidVisualizer - Real-time visualization of Braid tracking data using ReRun.

This module can be run as a standalone script or imported and used within another application.
"""

import argparse
import json
import multiprocessing as mp
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import rerun as rr
import rerun.blueprint as rrb
import zmq

# from rerun.blueprint import Blueprint, Row, Column, Spatial2DView, TimeSeriesView

try:
    # First try the normal import
    from src.utils.config import ZMQConfig
    from src.utils.worker_process import WorkerProcess
except ImportError:
    # If that fails, try to adjust the path and import again
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir

    # Keep going up until we find the right directory
    # (with a 'src' subdirectory or until we hit the root)
    while not (project_root / "src").exists() and project_root.parent != project_root:
        project_root = project_root.parent

    if (project_root / "src").exists():
        # Add the project root to the path
        sys.path.insert(0, str(project_root))

        # Now try the import again
        from src.utils.config import ZMQConfig
        from src.utils.worker_process import WorkerProcess


@dataclass
class ObjectTrackingData:
    """Stores tracking data for a single object."""

    obj_id: int
    timestamps: List[float] = None
    positions: List[Tuple[float, float, float]] = None
    velocities: List[Tuple[float, float, float]] = None
    headings: List[float] = None
    color: Tuple[float, float, float, float] = None

    def __post_init__(self):
        """Initialize lists if they're None."""
        self.timestamps = [] if self.timestamps is None else self.timestamps
        self.positions = [] if self.positions is None else self.positions
        self.velocities = [] if self.velocities is None else self.velocities
        self.headings = [] if self.headings is None else self.headings

        # Generate a deterministic color based on object ID
        if self.color is None:
            import colorsys

            h = (self.obj_id * 0.618033988749895) % 1.0  # Golden ratio distribution
            r, g, b = colorsys.hsv_to_rgb(h, 0.8, 0.95)
            self.color = (r, g, b, 1.0)

    def add_data_point(
        self,
        timestamp: float,
        position: Tuple[float, float, float],
        velocity: Tuple[float, float, float],
    ) -> None:
        """Add a new data point for this object."""
        self.timestamps.append(timestamp)
        self.positions.append(position)
        self.velocities.append(velocity)

        # Calculate heading from velocity
        vx, vy, _ = velocity
        heading = np.arctan2(vy, vx)
        self.headings.append(heading)

        # Trim data to keep only the last 10 seconds
        self._trim_old_data(timestamp - 10.0)

    def _trim_old_data(self, min_timestamp: float) -> None:
        """Remove data points older than min_timestamp."""
        if not self.timestamps:
            return

        # Find the index of the first timestamp that's newer than min_timestamp
        idx = 0
        while idx < len(self.timestamps) and self.timestamps[idx] < min_timestamp:
            idx += 1

        if idx > 0:
            self.timestamps = self.timestamps[idx:]
            self.positions = self.positions[idx:]
            self.velocities = self.velocities[idx:]
            self.headings = self.headings[idx:]

    def get_linear_velocity(self) -> List[float]:
        """Calculate linear velocity magnitude for each data point."""
        return [np.sqrt(vx**2 + vy**2 + vz**2) for vx, vy, vz in self.velocities]


class BraidVisualizer(WorkerProcess):
    """
    Real-time visualization of Braid tracking data using ReRun.
    """

    def __init__(
        self,
        config_path: str = "config.toml",
        event: Optional[mp.Event] = None,
        process_name: str = "BraidVisualizer",
        log_level: str = "INFO",
        log_color: str = "cyan",
        window_duration: float = 10.0,
    ):
        super().__init__(event, log_level, log_color, process_name)

        # Load configuration
        if isinstance(config_path, dict):
            self.config = config_path
        else:
            self.config = ZMQConfig(config_path)

        self.stop_event = event
        # Set up data storage
        self.tracked_objects = {}  # Maps obj_id to ObjectTrackingData
        self.window_duration = window_duration
        self.subscriber = None
        self.rr_initialized = False

        # ReRun client
        self.recording_id = f"braid_visualization_{int(time.time())}"

        # initialize logger
        self._initialize_logger()
        self.logger.info("BraidVisualizer initialized")

    def initialize(self) -> bool:
        """Initialize the visualizer."""
        try:
            # Initialize ZMQ
            self._connect_to_braid_server()

            # Initialize ReRun
            self._setup_rerun()

            self.logger.info("BraidVisualizer initialized successfully")
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize BraidVisualizer: {e}")
            return False

    def _connect_to_braid_server(self) -> None:
        """Connect to the Braid ZMQ server."""
        try:
            self.context = zmq.Context()

            # Set up subscriber to receive from Braid
            self.subscriber = self.context.socket(zmq.SUB)

            # Extract ZMQ settings from config
            subscriber_address = self.config.get_subscriber_address(
                self.config.braid_port
            )
            topic = self.config.braid_topic

            self.logger.info(f"Connecting ZMQ subscriber to {subscriber_address}")
            self.subscriber.connect(subscriber_address)

            # Subscribe to Braid messages
            self.subscriber.setsockopt_string(zmq.SUBSCRIBE, topic)
            self.logger.info(f"Subscribed to topic: {topic}")

        except Exception as e:
            self.logger.error(f"Failed to connect to Braid server: {e}")
            raise

    def _setup_rerun(self) -> None:
        """Initialize the ReRun visualization."""
        try:
            # Initialize ReRun
            blueprint = rrb.Blueprint(
                rrb.Horizontal(
                    rrb.Spatial2DView(),
                    rrb.Horizontal(
                        rrb.TimeSeriesView(),
                        rrb.TimeSeriesView(),
                    ),
                )
            )

            rr.init("BraidVisualizer", spawn=True, default_blueprint=blueprint)

            # Set coordinate system
            rr.log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP)

            self.rr_initialized = True
            self.logger.info("ReRun visualization initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize ReRun: {e}")
            raise

    def process_message(self, message_data: Dict[str, Any]) -> None:
        """Process a message from the Braid server."""
        print(f"Processing message: {message_data}")
        try:
            # Check message type (Birth, Update, Death)
            if "Birth" in message_data:
                self._process_birth(message_data["Birth"])
            elif "Update" in message_data:
                self._process_update(message_data["Update"])
            elif "Death" in message_data:
                self._process_death(message_data["Death"])
            else:
                self.logger.warning(
                    f"Unknown message type: {list(message_data.keys())}"
                )
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    def _process_birth(self, data: Dict[str, Any]) -> None:
        """Process a Birth message for a new tracked object."""
        try:
            obj_id = data["obj_id"]
            timestamp = data["timestamp"]

            # Create new tracked object
            self.tracked_objects[obj_id] = ObjectTrackingData(obj_id=obj_id)

            # Add initial data point
            position = (data["x"], data["y"], data["z"])
            velocity = (data["xvel"], data["yvel"], data["zvel"])
            self.tracked_objects[obj_id].add_data_point(timestamp, position, velocity)

            self.logger.debug(f"Started tracking object {obj_id}")

            # Update visualization
            self._update_visualization()

        except KeyError as e:
            self.logger.error(f"Missing field in Birth message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing Birth message: {e}")

    def _process_update(self, data: Dict[str, Any]) -> None:
        """Process an Update message for an existing tracked object."""
        try:
            obj_id = data["obj_id"]
            timestamp = data["timestamp"]

            # Check if we're already tracking this object
            if obj_id not in self.tracked_objects:
                self.logger.warning(
                    f"Received Update for unknown object {obj_id}, creating new tracking entry"
                )
                self._process_birth(data)  # Treat as Birth if not already tracking
                return

            # Update tracked object
            position = (data["x"], data["y"], data["z"])
            velocity = (data["xvel"], data["yvel"], data["zvel"])
            self.tracked_objects[obj_id].add_data_point(timestamp, position, velocity)

            # Update visualization
            self._update_visualization()

        except KeyError as e:
            self.logger.error(f"Missing field in Update message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing Update message: {e}")

    def _process_death(self, obj_id: int) -> None:
        """Process a Death message for a tracked object."""
        try:
            if obj_id in self.tracked_objects:
                self.logger.debug(f"Stopped tracking object {obj_id}")
                del self.tracked_objects[obj_id]

                # Update visualization
                self._update_visualization()
            else:
                self.logger.warning(f"Received Death for unknown object {obj_id}")
        except Exception as e:
            self.logger.error(f"Error processing Death message: {e}")

    def _update_visualization(self) -> None:
        """Update the ReRun visualization with the latest data."""
        if not self.rr_initialized or not self.tracked_objects:
            return

        current_time = time.time()

        # Log positions for the 2D overview
        for obj_id, obj_data in self.tracked_objects.items():
            if not obj_data.positions:
                continue

            # Get the latest position
            x, y, z = obj_data.positions[-1]

            # Log position with the object's color
            entity_path = f"objects/{obj_id}/position"
            rr.log(
                entity_path,
                rr.Points2D(
                    [[x, y]],
                    colors=[obj_data.color],
                    radii=[0.01],  # Adjust size as needed
                ),
                timeless=True,
            )

            # Log object ID as text
            rr.log(
                f"objects/{obj_id}/label", rr.TextLog(f"ID: {obj_id}"), timeless=True
            )

            # Log velocity arrow
            if obj_data.velocities and len(obj_data.velocities) > 0:
                vx, vy, _ = obj_data.velocities[-1]
                # Scale velocity for visualization
                scale = 0.5  # Adjust as needed

                rr.log(
                    f"objects/{obj_id}/velocity",
                    rr.Arrows2D(
                        origins=[[x, y]],
                        vectors=[[vx * scale, vy * scale]],
                        colors=[obj_data.color],
                    ),
                    timeless=True,
                )

            # Log velocity time series
            if len(obj_data.timestamps) > 1:
                linear_velocity = obj_data.get_linear_velocity()

                # Log velocity data
                rr.log(
                    f"timeseries/velocity/{obj_id}",
                    rr.TimeSeriesScalar(
                        times=obj_data.timestamps,
                        values=linear_velocity,
                        color=obj_data.color,
                    ),
                )

                # Log heading data (convert to degrees for better visualization)
                heading_degrees = [np.degrees(h) for h in obj_data.headings]
                rr.log(
                    f"timeseries/heading/{obj_id}",
                    rr.TimeSeriesScalar(
                        times=obj_data.timestamps,
                        values=heading_degrees,
                        color=obj_data.color,
                    ),
                )

    def run(self) -> None:
        """Main process loop."""
        if not self.initialize():
            self.logger.error("Failed to initialize, exiting process")
            return

        self.logger.info("BraidVisualizer running")

        # Set up poller for non-blocking receive
        poller = zmq.Poller()
        poller.register(self.subscriber, zmq.POLLIN)

        while not self.stop_event.is_set():
            # Poll for messages with timeout (100ms)
            socks = dict(poller.poll(100))

            if self.subscriber in socks and socks[self.subscriber] == zmq.POLLIN:
                # Process incoming message
                try:
                    # Receive multipart message (topic, content)
                    topic, message = self.subscriber.recv_multipart()
                    message_str = message.decode("utf-8")

                    # Parse JSON message
                    message_data = json.loads(message_str)

                    # Process the message
                    self.process_message(message_data)

                except json.JSONDecodeError as e:
                    self.logger.error(f"Error decoding JSON message: {e}")
                except Exception as e:
                    self.logger.error(f"Error processing message: {e}")

        # Clean up
        self.logger.info("BraidVisualizer stopping")
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources."""
        if self.subscriber:
            self.subscriber.close()

        # ReRun will clean up its own resources when the process exits


def run_as_script():
    """Run the visualizer as a standalone script."""
    parser = argparse.ArgumentParser(description="Braid data visualizer")
    parser.add_argument(
        "--config",
        "-c",
        default="/home/buchsbaum/src/OptoFly/config.toml",
        help="Path to config file",
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log level",
    )
    args = parser.parse_args()

    # Create and run visualizer
    stop_event = mp.Event()
    visualizer = BraidVisualizer(
        config_path=args.config, event=stop_event, log_level=args.log_level
    )

    try:
        visualizer.start()
        print(
            f"Visualizer started. View at: http://localhost:9876 (default ReRun port)"
        )
        print("Press Ctrl+C to stop...")

        # Wait for process to complete
        visualizer.join()
    except KeyboardInterrupt:
        print("\nStopping visualizer...")
        stop_event.set()
        visualizer.join(timeout=3)


# Function to start visualization in a separate process
def start_visualization(config_path="config.toml", log_level="INFO"):
    """
    Start the Braid visualizer in a separate process.

    Args:
        config_path: Path to config file
        log_level: Logging level

    Returns:
        Tuple of (process, stop_event)
    """
    stop_event = mp.Event()
    visualizer = BraidVisualizer(
        config_path=config_path, event=stop_event, log_level=log_level
    )

    visualizer.start()
    print(f"Visualizer started. View at: http://localhost:9876 (default ReRun port)")
    return visualizer, stop_event


if __name__ == "__main__":
    run_as_script()
