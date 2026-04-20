"""Static random pattern stimulus (QR-code-like background)."""

from typing import Dict, Any

import numpy as np
import pyglet

from src.stimuli.base import BaseStimulus


class StaticPatternStimulus(BaseStimulus):
    """Random static pattern resembling a QR code.

    Generates random squares once at startup, displays continuously.
    Open-loop stimulus (no interaction with tracking).
    """

    def __init__(
        self,
        config: Dict[str, Any],
        screen_width: int = 7680,
        screen_height: int = 1080,
    ):
        """Initialize static pattern from config.

        Args:
            config: Configuration from [visual_stimuli.static] section
            screen_width: Full display width in pixels
            screen_height: Full display height in pixels
        """
        super().__init__(config)

        # Parse configuration
        self.enabled = config.get("enabled", True)
        self.square_color = self._parse_color(config.get("square_color", "black"))
        self.background_color = self._parse_color(
            config.get("background_color", "white")
        )
        self.pattern_density = max(0.0, min(1.0, config.get("pattern_density", 0.3)))
        self.downscale_factor = max(1, config.get("downscale_factor", 2))
        self.random_seed = config.get("random_seed", None)

        # Screen dimensions
        self.screen_width = screen_width
        self.screen_height = screen_height

        # Track initialization state
        self._initialized = False
        self._batch_ref = None

        # Generate pattern once
        self.sprite = None
        self.image_data = None
        if self.enabled:
            self._generate_pattern()

    def _generate_pattern(self) -> None:
        """Generate random binary pattern as a single sprite.

        Uses pure numpy (no Pillow dependency) to build an RGB image,
        upscale with nearest-neighbor via np.repeat, and hand the bytes
        to pyglet.
        """
        # Calculate working resolution (ceil so np.repeat always covers the target)
        working_width = -(-self.screen_width // self.downscale_factor)
        working_height = -(-self.screen_height // self.downscale_factor)

        # Set random seed for reproducibility
        rng = np.random.default_rng(self.random_seed)

        # Generate binary matrix and map to RGB colors
        mask = rng.random((working_height, working_width)) < self.pattern_density
        bg = np.array(self.background_color, dtype=np.uint8)
        fg = np.array(self.square_color, dtype=np.uint8)
        image = np.where(mask[:, :, None], fg, bg)  # (H, W, 3)

        # Upscale to full resolution (nearest-neighbor)
        if self.downscale_factor > 1:
            image = np.repeat(image, self.downscale_factor, axis=0)
            image = np.repeat(image, self.downscale_factor, axis=1)
            # Trim to exact target size in case of rounding
            image = image[: self.screen_height, : self.screen_width]

        # Flip vertically — pyglet expects bottom-to-top row order
        image = image[::-1].copy()

        raw_bytes = image.tobytes()
        self.image_data = pyglet.image.ImageData(
            width=self.screen_width,
            height=self.screen_height,
            fmt="RGB",
            data=raw_bytes,
            pitch=self.screen_width * 3,
        )

        # Create sprite (batch will be set during initialize_rendering)
        self.sprite = pyglet.sprite.Sprite(img=self.image_data, x=0, y=0)

    def initialize_rendering(self, batch: pyglet.graphics.Batch) -> None:
        """Add sprite to batch once (called during setup).

        Args:
            batch: Pyglet graphics batch
        """
        if not self._initialized and self.enabled and self.sprite:
            self.sprite.batch = batch
            self._initialized = True
            self._batch_ref = batch

    def render(self, batch: pyglet.graphics.Batch) -> None:
        """No-op for static pattern - sprite already in batch.

        Static sprite is added to batch during initialize_rendering()
        and remains there throughout the application lifetime.

        Args:
            batch: Pyglet graphics batch (unused)
        """
        # Nothing to do - static sprite persists in batch
        pass

    def is_active(self) -> bool:
        """Return True if stimulus should be rendered.

        Returns:
            Always True if enabled
        """
        return self.enabled

    def cleanup(self) -> None:
        """Delete sprite and free resources."""
        if self.sprite:
            self.sprite.delete()
            self.sprite = None
        self.image_data = None
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
                "blue": (0, 0, 255),
            }
            return color_map.get(color.lower(), (0, 0, 0))
        else:
            return tuple(color[:3])  # Take first 3 elements (RGB)
