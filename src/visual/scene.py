from direct.showbase.ShowBase import ShowBase
from panda3d.core import (
    WindowProperties,
    Camera,
    PerspectiveLens,
    NodePath,
    load_prc_file_data,
)


class ArenaScene(ShowBase):
    """Panda3D window with 4 perspective cameras for a 360° panoramic fly arena.

    One 7680×1080 window (or 1280×360 in standalone) is split into four 1920×1080
    display regions. Each camera sits at origin and covers a 90° FOV quadrant.
    Units are centimeters throughout. Fly is at origin; North=+Y, East=+X, Z=up.
    """

    # Display region order left-to-right: North, East, South, West
    CAMERA_HEADINGS = [0.0, 90.0, 180.0, 270.0]

    def __init__(
        self,
        viewing_distance_cm: float = 25.0,
        standalone: bool = False,
    ):
        self.viewing_distance_cm = viewing_distance_cm
        self.standalone = standalone

        if standalone:
            width, height = 1280, 360
        else:
            width, height = 7680, 1080

        # Set window size via Panda3D config before ShowBase creates the window
        load_prc_file_data("", f"win-size {width} {height}\n")

        ShowBase.__init__(self)
        self.disableMouse()

        self._panel_width = width // 4
        self._panel_height = height

        self.cameras: list[NodePath] = []
        self._setup_cameras()

    def _setup_cameras(self) -> None:
        """Create 4 perspective cameras, each assigned to a display region."""
        panel_fraction = 1.0 / len(self.CAMERA_HEADINGS)

        for i, heading_deg in enumerate(self.CAMERA_HEADINGS):
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
