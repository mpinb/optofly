from direct.showbase.ShowBase import ShowBase
from panda3d.core import (
    Camera,
    PerspectiveLens,
    NodePath,
    load_prc_file_data,
)


DIRECTION_TO_HEADING: dict[str, float] = {
    "North": 0.0,
    "East": 90.0,
    "South": 180.0,
    "West": 270.0,
}


class ArenaScene(ShowBase):
    """Panda3D window with 4 perspective cameras for a 360° panoramic fly arena.

    One 7680×1080 window (or 1280×360 in standalone) is split into four 1920×1080
    display regions. Each camera sits at origin and covers a 90° FOV quadrant.
    Units are centimeters throughout. Fly is at origin; North=+Y, East=+X, Z=up.
    """

    def __init__(
        self,
        viewing_distance_cm: float = 25.0,
        camera_headings: list[float] | None = None,
        window_x_offset: int = 0,
        standalone: bool = False,
    ):
        self.viewing_distance_cm = viewing_distance_cm
        self.standalone = standalone

        if camera_headings is None:
            camera_headings = [0.0, 90.0, 180.0, 270.0]
        self._camera_headings = camera_headings

        if standalone:
            width, height = 1280, 360
            window_x_offset = 0
        else:
            width, height = 7680, 1080

        # Set window size and position via Panda3D config before ShowBase
        if standalone:
            load_prc_file_data(
                "",
                f"win-size {width} {height}\n"
                f"win-origin {window_x_offset} 0\n",
            )
        else:
            load_prc_file_data(
                "",
                f"win-size {width} {height}\n"
                f"win-origin {window_x_offset} 0\n"
                "undecorated #t\n"
                "cursor-hidden #t\n",
            )

        ShowBase.__init__(self)
        self.disableMouse()

        # On X11, override-redirect bypasses the window manager so the
        # window can span all four experimental screens without being
        # clamped or decorated.
        if not standalone:
            self._set_override_redirect()

        self._panel_width = width // 4
        self._panel_height = height

        self.cameras: list[NodePath] = []
        self._setup_cameras()

    def _set_override_redirect(self) -> None:
        """Set X11 override-redirect so the window manager leaves us alone."""
        import ctypes
        import ctypes.util

        try:
            xlib_path = ctypes.util.find_library("X11")
            if xlib_path is None:
                return
            xlib = ctypes.CDLL(xlib_path)

            window_id = self.win.getWindowHandle()
            if window_id is None:
                return

            # XSetWindowAttributes with only override_redirect
            CWOverrideRedirect = 1 << 9
            xlib.XChangeWindowAttributes.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
            ]

            class XSetWindowAttributes(ctypes.Structure):
                _fields_ = [
                    ("background_pixmap", ctypes.c_ulong),
                    ("background_pixel", ctypes.c_ulong),
                    ("border_pixmap", ctypes.c_ulong),
                    ("border_pixel", ctypes.c_ulong),
                    ("bit_gravity", ctypes.c_int),
                    ("win_gravity", ctypes.c_int),
                    ("backing_store", ctypes.c_int),
                    ("backing_planes", ctypes.c_ulong),
                    ("backing_pixel", ctypes.c_ulong),
                    ("save_under", ctypes.c_int),
                    ("event_mask", ctypes.c_long),
                    ("do_not_propagate_mask", ctypes.c_long),
                    ("override_redirect", ctypes.c_int),
                    ("colormap", ctypes.c_ulong),
                    ("cursor", ctypes.c_ulong),
                ]

            attrs = XSetWindowAttributes()
            attrs.override_redirect = 1

            display = xlib.XOpenDisplay(None)
            xlib.XChangeWindowAttributes(
                display, window_id, CWOverrideRedirect, ctypes.byref(attrs),
            )
            xlib.XFlush(display)
        except Exception:
            pass  # not X11, or display unavailable

    def _setup_cameras(self) -> None:
        """Create 4 perspective cameras, each assigned to a display region."""
        panel_fraction = 1.0 / len(self._camera_headings)

        for i, heading_deg in enumerate(self._camera_headings):
            left = i * panel_fraction
            right = (i + 1) * panel_fraction

            # Reuse the first default region (already at 0-1), create new ones for the rest
            if i == 0:
                region = self.win.getDisplayRegion(0)
                region.setDimensions(left, right, 0.0, 1.0)
            else:
                region = self.win.makeDisplayRegion(left, right, 0.0, 1.0)

            region.setClearColorActive(True)
            region.setClearColor((0.5, 0.5, 0.5, 1))

            cam_node = Camera(f"arena_cam_{i}")
            lens = PerspectiveLens()
            lens.setFov(90.0)
            lens.setAspectRatio(self._panel_width / self._panel_height)
            lens.setNearFar(0.1, 2000.0)
            cam_node.setLens(lens)

            cam_np = self.render.attachNewNode(cam_node)
            cam_np.setPos(0, 0, 0)
            cam_np.setH(heading_deg)

            region.setCamera(cam_np)
            self.cameras.append(cam_np)

    def cleanup(self) -> None:
        """Shut down Panda3D cleanly (use in tests and process cleanup)."""
        import builtins

        # Remove the global base reference to allow new instances
        if hasattr(builtins, 'base'):
            delattr(builtins, 'base')

        # Clean up Panda3D resources
        try:
            self.finalizeExit()
        except SystemExit:
            pass  # finalizeExit() calls sys.exit(), catch it for tests
