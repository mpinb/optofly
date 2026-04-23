"""Vertical bar stimulus for testing fly reorientation behavior.

Presents a vertical bar at a configurable angular position relative to
the fly's heading when triggered. Used to test attractive/repulsive
responses and reorientation behavior.
"""

import random
import time
import numpy as np
import pyglet.shapes
from typing import Dict, Any, List
from src.stimuli.base import BaseStimulus
from src.stimuli.geometry import GeometryUtils


class VerticalBarStimulus(BaseStimulus):
    """Vertical bar stimulus for reorientation experiments.

    Closed-loop stimulus that presents a vertical bar at a specified
    angular position relative to the fly's heading. Can be configured
    to appear to the left (-90°), right (+90°), ahead (0°), or behind (±180°).

    Typical use case: Test if fly reorients toward an attractive stimulus
    presented on its sides.
    """

    # State machine constants
    IDLE = 0  # Waiting for trigger, no bar shown
    ACTIVE = 1  # Bar is currently displayed
    COOLDOWN = 2  # Bar hidden, waiting before can trigger again

    def __init__(
        self,
        config: Dict[str, Any],
        geometry_utils: GeometryUtils,
        logger,
        csv_writer,
        window_height: int = None,
    ):
        """Initialize vertical bar stimulus.

        Args:
            config: Configuration dictionary from [visual_stimuli.vertical_bar] section
            geometry_utils: GeometryUtils instance for coordinate conversion
            logger: Logger instance for console output
            csv_writer: CSVWriter instance for event logging
            window_height: Actual window height in pixels (for rendering)
        """
        # Call parent constructor (BaseStimulus.__init__)
        super().__init__(config)

        # Store dependencies
        self.geometry = geometry_utils  # For heading→pixel conversion
        self.logger = logger  # For logging messages
        self.csv_writer = csv_writer  # For recording events to CSV

        # Store actual window dimensions for rendering
        # Use window_height if provided, otherwise fall back to geometry.screen_height
        self.window_height = (
            window_height if window_height is not None else geometry_utils.screen_height
        )

        # ============================================================
        # PARSE CONFIGURATION PARAMETERS
        # ============================================================

        # Basic enable/disable
        self.enabled = config.get("enabled", True)

        self.sham_probability: float = config.get("sham_probability", 0.0)

        # Bar appearance
        # -------------------------------------------------------------
        # Bar width in degrees (angular size as seen by fly)
        bar_width_deg = config.get("bar_width_deg", 20.0)
        # Convert to pixels using geometry utils (handles scaling automatically)
        self.bar_width_px = self.geometry.degrees_to_pixels(bar_width_deg)

        # Bar height as fraction of screen height (0.0 to 1.0)
        # 1.0 means full screen height, 0.5 means half height, etc.
        self.bar_height_fraction = config.get("bar_height_fraction", 1.0)

        # Bar color (can be string name or RGB tuple)
        self.bar_color = self._parse_color(config.get("bar_color", "black"))

        # Bar position(s)
        # -------------------------------------------------------------
        # Angular positions in degrees relative to fly heading
        # Can be single value or list of options
        # Examples:
        #   - 90 means 90° to fly's right
        #   - -90 means 90° to fly's left
        #   - 0 means directly ahead
        #   - 180 or -180 means directly behind
        self.positions_deg = self._parse_positions(config.get("positions_deg", [90]))

        # Timing parameters
        # -------------------------------------------------------------
        # How long to display the bar (milliseconds)
        self.display_duration_ms_options = self._parse_parameter(
            config.get("display_duration_ms", 300), "display_duration_ms"
        )

        # Cooldown period before bar can appear again (milliseconds)
        self.cooldown_duration_ms_options = self._parse_parameter(
            config.get("cooldown_duration_ms", 10000), "cooldown_duration_ms"
        )

        # Currently selected values (set when triggered)
        self.display_duration_ms = None
        self.cooldown_duration_ms = None

        # ============================================================
        # STATE MACHINE INITIALIZATION
        # ============================================================

        # Current state (starts in IDLE)
        self.state = self.IDLE

        # Time elapsed in current state (seconds)
        self.elapsed_time = 0.0

        # ============================================================
        # POSITION TRACKING
        # ============================================================

        # Screen position of bar (pixels)
        self.bar_x = 0  # Left edge of bar
        self.bar_y = 0  # Bottom edge of bar

        # Actual bar dimensions (calculated at runtime)
        self.actual_bar_height = 0

        # ============================================================
        # POSITION BALANCING
        # ============================================================

        # Track how many times each position has been used
        # Ensures balanced presentation across positions
        # Example: {-90: 0, 0: 0, 90: 0}
        self.position_counts = {pos: 0 for pos in self.positions_deg}

        # ============================================================
        # RENDERING OBJECTS
        # ============================================================

        # Main bar rectangle (None until created)
        self.bar_rectangle = None

        # Wrapped bar for edge wrapping (None until needed)
        self.wrapped_rectangle = None

        # ============================================================
        # BATCH AND INITIALIZATION TRACKING
        # ============================================================

        # Reference to pyglet batch (set during initialize_rendering)
        self._batch_ref = None

        # Whether initialize_rendering has been called
        self._initialized = False

        # ============================================================
        # TRIGGER DATA STORAGE
        # ============================================================

        # Store trigger data for logging (set in on_trigger)
        self.trigger_data = None

        # Selected position for current presentation (degrees)
        self.selected_position_deg = None

    def _parse_parameter(self, value, param_name: str) -> List:
        """Parse parameter that can be single value or list of options.

        This allows configuration like:
            display_duration_ms = 2000           # Single value
        or:
            display_duration_ms = [1000, 2000, 3000]  # Random selection

        Args:
            value: Either a single number or a list of numbers
            param_name: Name of parameter (for error messages)

        Returns:
            List of possible values (even if input was single value)

        Raises:
            ValueError: If list is empty
        """
        if isinstance(value, list):
            if len(value) == 0:
                raise ValueError(f"{param_name} cannot be an empty list")
            return value
        else:
            # Single value - wrap in list
            return [value]

    def _parse_positions(self, positions) -> List[float]:
        """Parse position offsets and validate.

        Args:
            positions: Single position or list of positions (degrees)

        Returns:
            List of position offsets in degrees

        Raises:
            ValueError: If positions list is empty
        """
        # Convert to list if single value
        if not isinstance(positions, list):
            positions = [positions]

        # Validate not empty
        if len(positions) == 0:
            raise ValueError("positions_deg cannot be empty")

        return positions

    def initialize_rendering(self, batch: pyglet.graphics.Batch) -> None:
        """Store batch reference for later use.

        Rectangles are created on-demand when triggered, not during
        initialization, since this is a closed-loop stimulus.

        Args:
            batch: Pyglet graphics batch for rendering
        """
        self._batch_ref = batch
        self._initialized = True

        # Calculate actual bar height from fraction
        # bar_height_fraction = 1.0 means full screen height
        self.actual_bar_height = int(self.bar_height_fraction * self.window_height)

    def on_trigger(self, trigger_data: Dict[str, Any]) -> None:
        """Handle TRIGGER message - start bar presentation.

        Called when fly enters trigger zone. Activates the stimulus by:
        1. Selecting a balanced position
        2. Randomizing parameters (if configured)
        3. Calculating screen position
        4. Transitioning to ACTIVE state

        Args:
            trigger_data: Dictionary containing:
                - obj_id: Fly/object ID
                - frame: Camera frame number
                - braid_timestamp: Tracking system timestamp
                - trigger_timestamp: When trigger fired
                - mean_heading: Fly heading in radians
        """
        # ============================================================
        # CHECK IF ALREADY ACTIVE
        # ============================================================

        # Ignore trigger if we're not in IDLE state
        # (Bar already showing, or in cooldown)
        if self.state != self.IDLE:
            self.logger.warning(
                f"VerticalBar trigger ignored for obj_id={trigger_data['obj_id']} "
                f"- stimulus already active (state={self.state}, frame={trigger_data['frame']})"
            )
            return

        # Select all parameters before sham check so balanced randomization is
        # maintained and the CSV records what would have been shown.
        self.selected_position_deg = self._select_balanced_position()
        self.display_duration_ms = np.random.choice(self.display_duration_ms_options)
        self.cooldown_duration_ms = np.random.choice(self.cooldown_duration_ms_options)

        fly_heading_rad = trigger_data["mean_heading"]
        self.bar_x = self.geometry.heading_to_pixel_x(
            fly_heading_rad, self.selected_position_deg
        )
        self.bar_y = (self.window_height - self.actual_bar_height) // 2

        is_sham = random.random() < self.sham_probability

        self._log_stimulus_event(
            trigger_data, self.selected_position_deg, fly_heading_rad, is_sham
        )

        if is_sham:
            self.logger.info(f"Sham vertical_bar for obj_id={trigger_data['obj_id']}")
            return

        self.state = self.ACTIVE
        self.elapsed_time = 0.0
        self.trigger_data = trigger_data

        self.logger.info(
            f"VerticalBar started: obj_id={trigger_data['obj_id']}, "
            f"heading={np.rad2deg(fly_heading_rad):.1f}°, "
            f"position={self.selected_position_deg}°, "
            f"bar_x={self.bar_x}px, "
            f"duration={self.display_duration_ms}ms"
        )

    def update(self, dt: float) -> None:
        """Update state machine and timing.

        Called 240 times per second. Advances the state machine based on
        elapsed time.

        Args:
            dt: Time since last frame in seconds (~0.00417s at 240Hz)
        """
        # Skip if idle (nothing to update)
        if self.state == self.IDLE:
            return

        # ============================================================
        # UPDATE ELAPSED TIME
        # ============================================================

        # Accumulate time in current state
        self.elapsed_time += dt

        # ============================================================
        # STATE MACHINE LOGIC
        # ============================================================

        if self.state == self.ACTIVE:
            # Bar is currently displayed

            # Check if display duration has elapsed
            if self.elapsed_time >= self.display_duration_ms / 1000.0:
                # Time's up! Transition to COOLDOWN
                self.state = self.COOLDOWN
                self.elapsed_time = 0.0  # Reset timer for cooldown

                self.logger.debug(
                    f"VerticalBar entering cooldown (duration={self.cooldown_duration_ms}ms)"
                )

        elif self.state == self.COOLDOWN:
            # Bar is hidden, waiting before can trigger again

            # Check if cooldown duration has elapsed
            if self.elapsed_time >= self.cooldown_duration_ms / 1000.0:
                # Cooldown complete! Back to IDLE
                self.state = self.IDLE
                self.elapsed_time = 0.0  # Reset timer

                # Clean up rectangles
                self.bar_rectangle = None
                self.wrapped_rectangle = None

                self.logger.debug("VerticalBar returned to IDLE")

    def render(self, batch: pyglet.graphics.Batch) -> None:
        """Update or create bar rectangles.

        Called 240 times per second. Creates or updates the bar rectangle
        based on current state.

        Args:
            batch: Pyglet graphics batch for rendering
        """
        # ============================================================
        # IDLE STATE - HIDE BAR
        # ============================================================

        if self.state == self.IDLE or self.state == self.COOLDOWN:
            # Bar should not be visible
            self._hide_rectangles()
            return

        # ============================================================
        # ACTIVE STATE - SHOW BAR
        # ============================================================

        # At this point, self.state == ACTIVE

        # Create or update main bar rectangle
        if self.bar_rectangle is None:
            # First frame - create new rectangle
            self.bar_rectangle = pyglet.shapes.Rectangle(
                x=self.bar_x,
                y=self.bar_y,
                width=self.bar_width_px,
                height=self.actual_bar_height,
                color=self.bar_color,
                batch=batch,
            )
        else:
            # Subsequent frames - update existing rectangle
            # (Only needed if bar moves, but good practice)
            self.bar_rectangle.x = self.bar_x
            self.bar_rectangle.y = self.bar_y
            self.bar_rectangle.width = self.bar_width_px
            self.bar_rectangle.height = self.actual_bar_height

        # ============================================================
        # EDGE WRAPPING
        # ============================================================

        # Check if bar extends beyond screen edges
        # If so, create wrapped rectangle on opposite side
        if self._needs_wrapping():
            wrapped_x = self._get_wrapped_x()

            if self.wrapped_rectangle is None:
                # Create wrapped rectangle
                self.wrapped_rectangle = pyglet.shapes.Rectangle(
                    x=wrapped_x,
                    y=self.bar_y,
                    width=self.bar_width_px,
                    height=self.actual_bar_height,
                    color=self.bar_color,
                    batch=batch,
                )
            else:
                # Update wrapped rectangle
                self.wrapped_rectangle.x = wrapped_x
                self.wrapped_rectangle.y = self.bar_y
                self.wrapped_rectangle.width = self.bar_width_px
                self.wrapped_rectangle.height = self.actual_bar_height
        else:
            # No wrapping needed - hide wrapped rectangle if it exists
            self._hide_wrapped_rectangle()

    def _hide_rectangles(self) -> None:
        """Remove all rectangles from batch.

        Called when bar should not be visible (IDLE or COOLDOWN).
        """
        if self.bar_rectangle is not None:
            self.bar_rectangle.delete()  # Remove from batch
            self.bar_rectangle = None  # Allow garbage collection

        if self.wrapped_rectangle is not None:
            self.wrapped_rectangle.delete()
            self.wrapped_rectangle = None

    def _hide_wrapped_rectangle(self) -> None:
        """Remove wrapped rectangle from batch.

        Called when edge wrapping is not needed.
        """
        if self.wrapped_rectangle is not None:
            self.wrapped_rectangle.delete()
            self.wrapped_rectangle = None

    def is_active(self) -> bool:
        """Return True if stimulus should be rendered.

        Returns:
            True if enabled and in ACTIVE state
        """
        return self.enabled and self.state == self.ACTIVE

    def _select_balanced_position(self) -> float:
        """Select position offset with least usage.

        Implements balanced presentation algorithm:
        1. Find minimum usage count
        2. Get all positions with that count
        3. Randomly select from those candidates
        4. Increment selected position's count

        Example:
            position_counts = {-90: 5, 0: 3, 90: 3}
            → min_count = 3
            → candidates = [0, 90]
            → randomly select one (e.g., 90)
            → increment: position_counts[90] = 4
            → return 90

        Returns:
            Selected position offset in degrees
        """
        # Find minimum usage count across all positions
        min_count = min(self.position_counts.values())

        # Get all positions that have minimum count
        candidates = [
            pos for pos, count in self.position_counts.items() if count == min_count
        ]

        # Randomly select from candidates (breaks ties randomly)
        selected = np.random.choice(candidates)

        # Increment usage count for selected position
        self.position_counts[selected] += 1

        return selected

    def _needs_wrapping(self) -> bool:
        """Check if bar crosses screen edge.

        Bar wraps if its left edge is before pixel 0 or its right edge
        is after the screen width.

        Returns:
            True if bar needs edge wrapping
        """
        screen_width = self.geometry.screen_width
        bar_right_edge = self.bar_x + self.bar_width_px

        return (
            (self.bar_x < 0)  # Left edge off screen
            or (bar_right_edge > screen_width)  # Right edge off screen
        )

    def _get_wrapped_x(self) -> int:
        """Calculate x-coordinate for wrapped rectangle.

        If bar extends off left edge, render duplicate on right side.
        If bar extends off right edge, render duplicate on left side.

        Returns:
            X-coordinate for wrapped rectangle
        """
        screen_width = self.geometry.screen_width

        if self.bar_x < 0:
            # Bar extends off left edge
            # Render duplicate on right side
            return self.bar_x + screen_width
        else:
            # Bar extends off right edge
            # Render duplicate on left side
            return self.bar_x - screen_width

    def _log_stimulus_event(
        self,
        trigger_data: Dict[str, Any],
        selected_position_deg: float,
        fly_heading_rad: float,
        sham: bool,
    ) -> None:
        """Log complete stimulus parameters to CSV."""
        fly_heading_deg = np.rad2deg(fly_heading_rad)
        absolute_angle_deg = fly_heading_deg + selected_position_deg

        log_data = {
            "timestamp": time.time(),
            "trigger_timestamp": trigger_data.get("timestamp"),
            "obj_id": trigger_data["obj_id"],
            "frame": trigger_data["frame"],
            "stimulus_type": "vertical_bar",
            "sham": sham,
            "fly_heading_rad": fly_heading_rad,
            "fly_heading_deg": fly_heading_deg,
            "stimulus_offset_deg": selected_position_deg,
            "stimulus_absolute_angle_deg": absolute_angle_deg,
            "pixel_x": self.bar_x,
            "pixel_y": self.bar_y,
            "bar_width_px": self.bar_width_px,
            "bar_height_px": self.actual_bar_height,
            "bar_color": str(self.bar_color),
            "display_duration_ms": self.display_duration_ms,
            "cooldown_duration_ms": self.cooldown_duration_ms,
        }

        self.csv_writer.append(log_data)

    def _parse_color(self, color) -> tuple:
        """Convert string color name or RGB list to RGB tuple.

        Args:
            color: Color name string ("black", "white", etc.) or RGB list [r, g, b]

        Returns:
            RGB tuple (r, g, b) with values 0-255
        """
        if isinstance(color, str):
            # String color name - map to RGB
            color_map = {
                "black": (0, 0, 0),
                "white": (255, 255, 255),
                "gray": (128, 128, 128),
                "red": (255, 0, 0),
                "green": (0, 255, 0),
                "blue": (0, 0, 255),
                "yellow": (255, 255, 0),
                "cyan": (0, 255, 255),
                "magenta": (255, 0, 255),
            }
            return color_map.get(color.lower(), (0, 0, 0))
        else:
            # Already RGB list/tuple - convert to tuple
            return tuple(color[:3])  # Take first 3 elements

    def cleanup(self) -> None:
        """Clean up rectangle resources.

        Called during shutdown. Deletes all graphics objects and
        frees resources.
        """
        # Delete rectangles
        self._hide_rectangles()

        # Clear references
        self._batch_ref = None
        self._initialized = False

        self.logger.debug("VerticalBar cleanup complete")
