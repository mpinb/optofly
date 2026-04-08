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
        background_color: tuple = (255, 255, 255, 255),
        standalone: bool = False,
        standalone_width: int = 1280,
        standalone_height: int = 720,
        use_experimental_display: bool = False,
    ):
        """Initialize display manager.

        Args:
            window_x_offset: X position of window (start of experimental screens)
            window_width: Total width in pixels (production mode)
            window_height: Height in pixels (production mode)
            background_color: RGBA background color
            standalone: If True, create small testing window instead of fullscreen
            standalone_width: Width for standalone testing window
            standalone_height: Height for standalone testing window
            use_experimental_display: If True in standalone mode, render on experimental display
        """
        self.window_x_offset = window_x_offset
        self.window_width = window_width
        self.window_height = window_height
        self.background_color = background_color
        self.standalone = standalone
        self.standalone_width = standalone_width
        self.standalone_height = standalone_height
        self.use_experimental_display = use_experimental_display
        self.window = None

    def create_window(
        self, caption: str = "OptoFly Visual Stimuli"
    ) -> pyglet.window.Window:
        """Create window for visual stimuli display.

        Creates either:
        - Production mode: Fullscreen window on experimental screens
        - Standalone mode (experimental display): Full window on experimental screens with overlay
        - Standalone mode (main screen): Small testing window on main screen

        Args:
            caption: Window title

        Returns:
            Pyglet window instance
        """
        if self.standalone and self.use_experimental_display:
            # Standalone testing mode on experimental display
            # Use full experimental display dimensions
            self.window = pyglet.window.Window(
                width=self.window_width,
                height=self.window_height,
                caption=f"{caption} - Standalone (Experimental Display)",
                resizable=False,
                vsync=True,
            )
            # Set window position (move to experimental screens)
            self.window.set_location(self.window_x_offset, 0)

        elif self.standalone:
            # Standalone testing mode: small window on main screen
            self.window = pyglet.window.Window(
                width=self.standalone_width,
                height=self.standalone_height,
                caption=f"{caption} - Standalone Testing",
                resizable=False,
                vsync=True,
            )
            # Center on main screen (don't move to experimental screens)
            # Window will open at default position
        else:
            # Production mode: borderless window on experimental screens
            self.window = pyglet.window.Window(
                width=self.window_width,
                height=self.window_height,
                caption=caption,
                resizable=False,
                style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
                vsync=True,
            )
            self.window.set_location(self.window_x_offset, 0)

        # Set background clear color
        r, g, b, a = self.background_color
        pyglet.gl.glClearColor(r / 255, g / 255, b / 255, a / 255)

        return self.window

    def close(self) -> None:
        """Close the display window."""
        if self.window:
            self.window.close()
            self.window = None
