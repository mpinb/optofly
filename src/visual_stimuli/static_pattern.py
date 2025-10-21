"""Static random pattern stimulus (QR-code-like background)."""

import numpy as np
import pyglet.shapes
from typing import Dict, Any
from src.visual_stimuli.base_stimulus import BaseStimulus


class StaticPatternStimulus(BaseStimulus):
    """Random static pattern resembling a QR code.

    Generates random squares once at startup, displays continuously.
    Open-loop stimulus (no interaction with tracking).
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize static pattern from config.

        Args:
            config: Configuration from [visual_stimuli.static] section
        """
        super().__init__(config)

        # Parse configuration
        self.enabled = config.get("enabled", True)
        self.square_color = self._parse_color(config.get("square_color", "black"))
        self.background_color = self._parse_color(config.get("background_color", "white"))
        self.avg_size = config.get("average_square_size_px", 50)
        self.size_std = config.get("square_size_std_px", 15)
        self.num_squares = config.get("num_squares", 500)
        self.random_seed = config.get("random_seed", None)

        # Screen dimensions (full experimental display)
        self.screen_width = 7680
        self.screen_height = 1080

        # Generate pattern once
        self.rectangles = []
        if self.enabled:
            self._generate_pattern()

    def _generate_pattern(self) -> None:
        """Generate random square positions and sizes."""
        # Set random seed for reproducibility
        if self.random_seed is not None:
            np.random.seed(self.random_seed)

        # Generate random positions (uniform across display)
        positions_x = np.random.uniform(0, self.screen_width, self.num_squares)
        positions_y = np.random.uniform(0, self.screen_height, self.num_squares)

        # Generate random sizes (Gaussian distribution)
        sizes = np.abs(np.random.normal(self.avg_size, self.size_std, self.num_squares))

        # Create pyglet rectangles (but don't add to batch yet)
        for x, y, size in zip(positions_x, positions_y, sizes):
            # Note: Rectangles are created here but batch is set during render
            rect = pyglet.shapes.Rectangle(
                x=x,
                y=y,
                width=size,
                height=size,
                color=self.square_color
            )
            self.rectangles.append(rect)

    def render(self, batch: pyglet.graphics.Batch) -> None:
        """Add all squares to render batch.

        Args:
            batch: Pyglet graphics batch
        """
        if not self.is_active():
            return

        # Add each rectangle to the batch
        for rect in self.rectangles:
            rect.batch = batch

    def is_active(self) -> bool:
        """Return True if stimulus should be rendered.

        Returns:
            Always True if enabled
        """
        return self.enabled

    def _parse_color(self, color) -> tuple:
        """Convert string or RGB list to RGB tuple.

        Args:
            color: Color name string or RGB list/tuple

        Returns:
            RGB tuple (r, g, b)
        """
        if isinstance(color, str):
            color_map = {
                "black": (0, 0, 0),
                "white": (255, 255, 255),
                "gray": (128, 128, 128),
                "red": (255, 0, 0),
                "green": (0, 255, 0),
                "blue": (0, 0, 255)
            }
            return color_map.get(color.lower(), (0, 0, 0))
        else:
            return tuple(color[:3])  # Take first 3 elements (RGB)
