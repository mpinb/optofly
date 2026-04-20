"""Standalone testing controller for manual stimulus triggering."""

import math
import time
from typing import Optional
import pyglet
from pyglet.text import Label


class StandaloneController:
    """Manages keyboard input and debug overlay for standalone testing."""

    def __init__(self, window, registry, geometry, logger):
        """Initialize standalone controller.

        Args:
            window: Pyglet window for rendering
            registry: StimulusRegistry instance
            geometry: GeometryUtils instance
            logger: Logger instance
        """
        self.window = window
        self.registry = registry
        self.geometry = geometry
        self.logger = logger

        # State
        self.simulated_heading_deg = 0.0
        self.trigger_history = []  # List of (timestamp, heading, position)

        # Debug overlay labels
        self._create_overlay()

        # Register event handlers
        self.window.push_handlers(self.on_key_press)

        self.logger.info("Standalone controller initialized")

    def _create_overlay(self):
        """Create debug text overlay."""
        self.labels = {
            "heading": Label(
                "Heading: 0.0°",
                x=10,
                y=self.window.height - 30,
                font_size=14,
                color=(255, 255, 255, 255),
            ),
            "active": Label(
                "Active: None",
                x=10,
                y=self.window.height - 60,
                font_size=14,
                color=(255, 255, 255, 255),
            ),
            "help": Label(
                "[SPACE] Trigger | [←/→] Adjust heading | [1/2/3] Position | [R] Reset | [ESC] Exit",
                x=10,
                y=30,
                font_size=12,
                color=(200, 200, 200, 255),
            ),
            "mode": Label(
                "STANDALONE MODE",
                x=10,
                y=self.window.height - 90,
                font_size=14,
                color=(100, 255, 100, 255),
            ),
            "last_trigger": Label(
                "Last trigger: None",
                x=10,
                y=self.window.height - 120,
                font_size=12,
                color=(200, 200, 200, 255),
            ),
        }

    def on_key_press(self, symbol, modifiers):
        """Handle keyboard input.

        Args:
            symbol: Key symbol from pyglet
            modifiers: Key modifiers (shift, ctrl, etc.)

        Returns:
            True if event was handled
        """
        if symbol == pyglet.window.key.SPACE:
            self._trigger_stimulus()
        elif symbol == pyglet.window.key.LEFT:
            self.simulated_heading_deg -= 10
            self._normalize_heading()
            self.logger.info(f"Heading adjusted: {self.simulated_heading_deg:.1f}°")
        elif symbol == pyglet.window.key.RIGHT:
            self.simulated_heading_deg += 10
            self._normalize_heading()
            self.logger.info(f"Heading adjusted: {self.simulated_heading_deg:.1f}°")
        elif symbol == pyglet.window.key.R:
            self.simulated_heading_deg = 0.0
            self.logger.info("Reset heading to 0°")
        elif symbol == pyglet.window.key._1:
            self._trigger_stimulus(position_override=-90)
        elif symbol == pyglet.window.key._2:
            self._trigger_stimulus(position_override=0)
        elif symbol == pyglet.window.key._3:
            self._trigger_stimulus(position_override=90)
        elif symbol == pyglet.window.key.ESCAPE:
            self.logger.info("Exit requested")
            pyglet.app.exit()
            return True

        self._update_overlay()
        return True

    def _trigger_stimulus(self, position_override: Optional[float] = None):
        """Manually trigger stimulus with current heading.

        Args:
            position_override: Optional position offset in degrees.
                             If None, uses stimulus's default position selection.
        """
        # Create fake trigger data that mimics real TRIGGER messages
        trigger_data = {
            "obj_id": 9999,  # Fake ID for standalone testing
            "frame": int(time.time() * 240),  # Simulated frame number
            "braid_timestamp": time.time(),
            "trigger_timestamp": time.time(),
            "mean_heading": math.radians(self.simulated_heading_deg),
        }

        # Note: position_override is not part of standard trigger_data
        # Looming stimulus will use its own position balancing
        # For manual position control, we'd need to modify the stimulus classes

        # Send to registry
        self.registry.on_trigger(trigger_data)

        # Log
        pos_str = f" @ {position_override}°" if position_override else ""
        self.logger.info(
            f"Manual trigger: heading={self.simulated_heading_deg:.1f}°{pos_str}"
        )

        # Add to history
        self.trigger_history.append(
            (time.time(), self.simulated_heading_deg, position_override)
        )
        if len(self.trigger_history) > 10:
            self.trigger_history.pop(0)

    def _normalize_heading(self):
        """Keep heading in [0, 360) range."""
        self.simulated_heading_deg = self.simulated_heading_deg % 360

    def _update_overlay(self):
        """Update debug overlay text."""
        self.labels["heading"].text = f"Heading: {self.simulated_heading_deg:.1f}°"

        active_stimuli = self.registry.get_active_stimuli()
        active_str = ", ".join(active_stimuli) if active_stimuli else "None"
        self.labels["active"].text = f"Active: {active_str}"

        # Update last trigger info
        if self.trigger_history:
            last_time, last_heading, last_pos = self.trigger_history[-1]
            time_ago = time.time() - last_time
            pos_str = f" @ {last_pos}°" if last_pos else ""
            self.labels[
                "last_trigger"
            ].text = f"Last trigger: {last_heading:.1f}°{pos_str} ({time_ago:.1f}s ago)"

    def render_overlay(self):
        """Render debug overlay on top of stimuli."""
        for label in self.labels.values():
            label.draw()
