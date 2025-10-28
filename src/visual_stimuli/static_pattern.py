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
        self.pattern_density = max(0.0, min(1.0, config.get("pattern_density", 0.3)))
        self.downscale_factor = max(1, config.get("downscale_factor", 2))
        self.random_seed = config.get("random_seed", None)

        # Log warning if values were clamped
        if config.get("pattern_density", 0.3) != self.pattern_density:
            import logging
            logging.warning(f"pattern_density clamped to valid range [0.0, 1.0]: {self.pattern_density}")
        if config.get("downscale_factor", 2) != self.downscale_factor:
            import logging
            logging.warning(f"downscale_factor must be >=1, set to: {self.downscale_factor}")

        # Screen dimensions (full experimental display)
        self.screen_width = 7680
        self.screen_height = 1080

        # Track initialization state
        self._initialized = False
        self._batch_ref = None

        # Generate pattern once
        self.sprite = None
        self.image_data = None
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

    def initialize_rendering(self, batch: pyglet.graphics.Batch) -> None:
        """Add all rectangles to batch once (called during setup).

        Args:
            batch: Pyglet graphics batch
        """
        if not self._initialized and self.enabled:
            for rect in self.rectangles:
                rect.batch = batch
            self._initialized = True
            self._batch_ref = batch

    def render(self, batch: pyglet.graphics.Batch) -> None:
        """No-op for static pattern - shapes already in batch.

        Static rectangles are added to batch during initialize_rendering()
        and remain there throughout the application lifetime.

        Args:
            batch: Pyglet graphics batch (unused)
        """
        # Nothing to do - static shapes persist in batch
        pass

    def is_active(self) -> bool:
        """Return True if stimulus should be rendered.

        Returns:
            Always True if enabled
        """
        return self.enabled

    def cleanup(self) -> None:
        """Delete all rectangle shapes and free resources."""
        for rect in self.rectangles:
            rect.delete()
        self.rectangles = []
        self._initialized = False
        self._batch_ref = None

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
