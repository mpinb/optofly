"""Display window management for visual stimuli."""

import pyglet


class DisplayManager:
    """Manages pyglet window for stimulus display.

    Creates single fullscreen window spanning experimental screens
    (excludes control monitor).
    """

    def __init__(
        self,
        window_x_offset: int = 3840,
        window_width: int = 7680,
        window_height: int = 1080,
        background_color: tuple = (255, 255, 255, 255)
    ):
        """Initialize display manager.

        Args:
            window_x_offset: X position of window (start of experimental screens)
            window_width: Total width in pixels
            window_height: Height in pixels
            background_color: RGBA background color
        """
        self.window_x_offset = window_x_offset
        self.window_width = window_width
        self.window_height = window_height
        self.background_color = background_color
        self.window = None

    def create_window(self, caption: str = "OptoFly Visual Stimuli") -> pyglet.window.Window:
        """Create fullscreen window on experimental screens.

        Args:
            caption: Window title

        Returns:
            Pyglet window instance
        """
        # Create window at specified position
        self.window = pyglet.window.Window(
            width=self.window_width,
            height=self.window_height,
            caption=caption,
            resizable=False,
            vsync=True  # Enable VSync for 240Hz
        )

        # Set window position (move to experimental screens)
        self.window.set_location(self.window_x_offset, 0)

        # Set fullscreen on experimental displays
        # Note: This may need adjustment based on window manager
        # For now, we'll use borderless window at correct position
        self.window.set_fullscreen(False)  # Windowed mode

        # Set background clear color
        r, g, b, a = self.background_color
        pyglet.gl.glClearColor(r/255, g/255, b/255, a/255)

        return self.window

    def close(self) -> None:
        """Close the display window."""
        if self.window:
            self.window.close()
            self.window = None
