"""Looming stimulus with L/V ratio expansion dynamics."""

import time
import numpy as np
import pyglet.shapes
from typing import Dict, Any
from src.visual_stimuli.base_stimulus import BaseStimulus
from src.visual_stimuli.geometry_utils import GeometryUtils


class LoomingStimulusRenderer(BaseStimulus):
    """Looming stimulus - expanding circle simulating approaching threat.

    Closed-loop stimulus that responds to TRIGGER messages.
    Uses L/V ratio or exponential expansion dynamics.
    Balances presentation across configured positions.
    """

    # State machine constants
    IDLE = 0
    EXPANDING = 1
    HOLDING = 2

    def __init__(
        self,
        config: Dict[str, Any],
        geometry_utils: GeometryUtils,
        logger,
        csv_writer
    ):
        """Initialize looming stimulus.

        Args:
            config: Configuration from [visual_stimuli.looming] section
            geometry_utils: GeometryUtils instance for coordinate conversion
            logger: Logger instance
            csv_writer: CSVWriter instance for event logging
        """
        super().__init__(config)
        self.geometry = geometry_utils
        self.logger = logger
        self.csv_writer = csv_writer

        # Parse configuration
        self.enabled = config.get("enabled", True)

        # Parse parameters - can be single value or list of options
        self.initial_size_deg_options = self._parse_parameter(
            config.get("initial_size_deg", 5.0),
            "initial_size_deg"
        )
        self.final_size_deg_options = self._parse_parameter(
            config.get("final_size_deg", 80.0),
            "final_size_deg"
        )
        self.expansion_duration_ms_options = self._parse_parameter(
            config.get("expansion_duration_ms", 500),
            "expansion_duration_ms"
        )
        self.hold_time_ms_options = self._parse_parameter(
            config.get("hold_time_ms", 200),
            "hold_time_ms"
        )

        # Currently selected values (set when triggered)
        self.initial_size_deg = None
        self.final_size_deg = None
        self.expansion_duration_ms = None
        self.hold_time_ms = None

        self.expansion_type = config.get("expansion_type", "lv_ratio")
        self.lv_ratio_ms = config.get("lv_ratio_ms", 40.0)
        self.circle_color = self._parse_color(config.get("circle_color", "black"))
        self.positions_deg = config.get("positions_deg", [-90, 0, 90])

        # State machine
        self.state = self.IDLE
        self.elapsed_time = 0.0

        # Position tracking
        self.center_x = 0
        self.center_y = self.geometry.get_vertical_center()
        self.current_radius_px = 0

        # Position balancing
        self.position_counts = {pos: 0 for pos in self.positions_deg}

        # Rendering
        self.circle = None
        self.wrapped_circle = None  # For edge wrapping

        # Track batch and initialization
        self._batch_ref = None
        self._initialized = False

        # Trigger data (saved for logging)
        self.trigger_data = None
        self.selected_position_deg = None

    def _parse_parameter(self, value, param_name: str):
        """Parse parameter that can be either a single value or list of options.

        Args:
            value: Either a single number or a list of numbers
            param_name: Name of parameter (for error messages)

        Returns:
            List of possible values (even if input is single value)
        """
        if isinstance(value, list):
            if len(value) == 0:
                raise ValueError(f"{param_name} cannot be an empty list")
            return value
        else:
            # Single value - return as single-item list
            return [value]

    def initialize_rendering(self, batch: pyglet.graphics.Batch) -> None:
        """Store batch reference for later use.

        Circles are created on-demand when triggered, not during initialization,
        since looming is a closed-loop stimulus.

        Args:
            batch: Pyglet graphics batch
        """
        self._batch_ref = batch
        self._initialized = True

    def on_trigger(self, trigger_data: Dict[str, Any]) -> None:
        """Handle TRIGGER message - start looming presentation.

        Args:
            trigger_data: Trigger data with obj_id, frame, mean_heading, etc.
        """
        if self.state != self.IDLE:
            self.logger.warning(
                f"Looming trigger ignored for obj_id={trigger_data['obj_id']} "
                f"- stimulus already active (frame={trigger_data['frame']})"
            )
            return

        # Select balanced position
        self.selected_position_deg = self._select_balanced_position()

        # Calculate screen position
        fly_heading_rad = trigger_data['mean_heading']
        self.center_x = self.geometry.heading_to_pixel_x(
            fly_heading_rad,
            self.selected_position_deg
        )

        # Initialize expansion
        self.state = self.EXPANDING
        self.elapsed_time = 0.0
        self.trigger_data = trigger_data

        # Calculate initial radius
        self.current_radius_px = self.geometry.degrees_to_pixels(self.initial_size_deg)

        # Log event to CSV
        self._log_stimulus_event(trigger_data, self.selected_position_deg, fly_heading_rad)

        self.logger.info(
            f"Looming started: obj_id={trigger_data['obj_id']}, "
            f"heading={np.rad2deg(fly_heading_rad):.1f}°, "
            f"position={self.selected_position_deg}°, "
            f"screen_x={self.center_x}px"
        )

    def update(self, dt: float) -> None:
        """Update expansion state and radius.

        Args:
            dt: Time since last frame in seconds
        """
        if self.state == self.IDLE:
            return

        self.elapsed_time += dt

        if self.state == self.EXPANDING:
            # Check if expansion complete
            if self.elapsed_time >= self.expansion_duration_ms / 1000.0:
                self.state = self.HOLDING
                self.elapsed_time = 0.0
                self.current_radius_px = self.geometry.degrees_to_pixels(self.final_size_deg)
            else:
                # Calculate current radius
                if self.expansion_type == "lv_ratio":
                    self.current_radius_px = self._calculate_lv_radius(self.elapsed_time)
                else:
                    self.current_radius_px = self._calculate_exponential_radius(self.elapsed_time)

        elif self.state == self.HOLDING:
            # Check if hold complete
            if self.elapsed_time >= self.hold_time_ms / 1000.0:
                self.state = self.IDLE
                self.circle = None
                self.wrapped_circle = None

    def render(self, batch: pyglet.graphics.Batch) -> None:
        """Update existing circles or create if needed.

        Args:
            batch: Pyglet graphics batch
        """
        if self.state == self.IDLE:
            self._hide_circles()
            return

        # Create or update main circle
        if self.circle is None:
            self.circle = pyglet.shapes.Circle(
                x=self.center_x,
                y=self.center_y,
                radius=self.current_radius_px,
                color=self.circle_color,
                batch=batch
            )
        else:
            # Update existing circle properties instead of recreating
            self.circle.x = self.center_x
            self.circle.y = self.center_y
            self.circle.radius = self.current_radius_px

        # Handle edge wrapping
        if self._needs_wrapping():
            wrapped_x = self._get_wrapped_x()
            if self.wrapped_circle is None:
                self.wrapped_circle = pyglet.shapes.Circle(
                    x=wrapped_x,
                    y=self.center_y,
                    radius=self.current_radius_px,
                    color=self.circle_color,
                    batch=batch
                )
            else:
                # Update wrapped circle properties
                self.wrapped_circle.x = wrapped_x
                self.wrapped_circle.y = self.center_y
                self.wrapped_circle.radius = self.current_radius_px
        else:
            self._hide_wrapped_circle()

    def _hide_circles(self) -> None:
        """Remove circles from batch when stimulus is idle."""
        if self.circle is not None:
            self.circle.delete()
            self.circle = None
        if self.wrapped_circle is not None:
            self.wrapped_circle.delete()
            self.wrapped_circle = None

    def _hide_wrapped_circle(self) -> None:
        """Remove wrapped circle from batch when not needed."""
        if self.wrapped_circle is not None:
            self.wrapped_circle.delete()
            self.wrapped_circle = None

    def is_active(self) -> bool:
        """Return True if stimulus should be rendered.

        Returns:
            True if enabled and not idle
        """
        return self.enabled and self.state != self.IDLE

    def _select_balanced_position(self) -> float:
        """Select position offset with least usage (balanced presentation).

        Returns:
            Selected position offset in degrees
        """
        min_count = min(self.position_counts.values())
        candidates = [
            pos for pos, count in self.position_counts.items()
            if count == min_count
        ]
        selected = np.random.choice(candidates)
        self.position_counts[selected] += 1
        return selected

    def _calculate_lv_radius(self, t: float) -> int:
        """Calculate radius using L/V ratio equation.

        Standard equation: θ(t) = 2 * arctan(l / (v*t))
        Where l/v is the L/V ratio in seconds.

        Args:
            t: Time since expansion started (seconds)

        Returns:
            Radius in pixels
        """
        if t < 0.001:  # Avoid division by zero
            angular_size_rad = np.deg2rad(self.initial_size_deg)
        else:
            # lv_ratio in ms, convert to seconds
            lv_seconds = self.lv_ratio_ms / 1000.0
            # Calculate angular size using L/V equation
            angular_size_rad = 2 * np.arctan(lv_seconds / (2 * t))

        # Convert to degrees and then to pixels
        angular_size_deg = np.rad2deg(angular_size_rad)
        return self.geometry.degrees_to_pixels(angular_size_deg)

    def _calculate_exponential_radius(self, t: float) -> int:
        """Calculate radius using simple exponential growth.

        Args:
            t: Time since expansion started (seconds)

        Returns:
            Radius in pixels
        """
        # Linear interpolation between initial and final size
        progress = t / (self.expansion_duration_ms / 1000.0)
        progress = np.clip(progress, 0, 1)

        current_size_deg = (
            self.initial_size_deg +
            (self.final_size_deg - self.initial_size_deg) * progress
        )
        return self.geometry.degrees_to_pixels(current_size_deg)

    def _needs_wrapping(self) -> bool:
        """Check if circle crosses display edge.

        Returns:
            True if circle needs wrapping
        """
        screen_width = self.geometry.screen_width
        return (
            (self.center_x - self.current_radius_px < 0) or
            (self.center_x + self.current_radius_px > screen_width)
        )

    def _get_wrapped_x(self) -> int:
        """Calculate wrapped x-coordinate for edge wrapping.

        Returns:
            Wrapped x-coordinate
        """
        screen_width = self.geometry.screen_width
        if self.center_x - self.current_radius_px < 0:
            # Wraps off left edge - render duplicate on right
            return self.center_x + screen_width
        else:
            # Wraps off right edge - render duplicate on left
            return self.center_x - screen_width

    def _log_stimulus_event(
        self,
        trigger_data: Dict[str, Any],
        selected_position_deg: float,
        fly_heading_rad: float
    ) -> None:
        """Log complete stimulus parameters to CSV.

        Args:
            trigger_data: Trigger message data
            selected_position_deg: Selected position offset
            fly_heading_rad: Fly heading in radians
        """
        fly_heading_deg = np.rad2deg(fly_heading_rad)
        absolute_angle_deg = fly_heading_deg + selected_position_deg

        log_data = {
            "timestamp": time.time(),
            "obj_id": trigger_data["obj_id"],
            "frame": trigger_data["frame"],
            "braid_timestamp": trigger_data["braid_timestamp"],
            "trigger_timestamp": trigger_data["trigger_timestamp"],
            "stimulus_type": "looming",
            "fly_heading_rad": fly_heading_rad,
            "fly_heading_deg": fly_heading_deg,
            "stimulus_offset_deg": selected_position_deg,
            "stimulus_absolute_angle_deg": absolute_angle_deg,
            "pixel_x": self.center_x,
            "pixel_y": self.center_y,
            "initial_size_deg": self.initial_size_deg,
            "final_size_deg": self.final_size_deg,
            "expansion_duration_ms": self.expansion_duration_ms,
            "hold_time_ms": self.hold_time_ms,
            "expansion_type": self.expansion_type,
            "lv_ratio_ms": self.lv_ratio_ms,
            "circle_color": str(self.circle_color)
        }

        self.csv_writer.append(log_data)

    def _parse_color(self, color) -> tuple:
        """Convert string or RGB list to RGB tuple.

        Args:
            color: Color name or RGB list/tuple

        Returns:
            RGB tuple
        """
        if isinstance(color, str):
            color_map = {
                "black": (0, 0, 0),
                "white": (255, 255, 255),
                "red": (255, 0, 0),
                "green": (0, 255, 0),
                "blue": (0, 0, 255)
            }
            return color_map.get(color.lower(), (0, 0, 0))
        else:
            return tuple(color[:3])

    def cleanup(self) -> None:
        """Clean up circle resources."""
        self._hide_circles()
        self._batch_ref = None
        self._initialized = False
