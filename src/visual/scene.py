from direct.showbase.ShowBase import ShowBase
from panda3d.core import (
    Camera,
    PerspectiveLens,
    NodePath,
    WindowProperties,
    load_prc_file_data,
)


DIRECTION_TO_HEADING: dict[str, float] = {
    "North": 0.0,
    "East": 90.0,
    "South": 180.0,
    "West": 270.0,
}


class ArenaScene(ShowBase):
    """Panda3D window with 4 perspective cameras for a 360 panoramic fly arena.

    One 7680x1080 window (or 1280x360 in standalone) is split into four 1920x1080
    display regions. Each camera sits at origin and covers a 90 deg FOV quadrant.
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

        load_prc_file_data("", f"win-size {width} {height}\n")

        ShowBase.__init__(self)
        self.disableMouse()

        # Position the window and strip decorations.  PRC variables for
        # win-origin and undecorated are sometimes ignored by window managers;
        # setting them via WindowProperties as well gives us a second chance.
        if not standalone:
            props = WindowProperties()
            props.setOrigin(window_x_offset, 0)
            props.setSize(width, height)
            props.setUndecorated(True)
            props.setCursorHidden(True)
            self.win.requestProperties(props)

        self._panel_width = width // 4
        self._panel_height = height

        self.cameras: list[NodePath] = []
        self._setup_cameras()

    def _setup_cameras(self) -> None:
        """Create 4 perspective cameras, each assigned to a display region."""
        panel_fraction = 1.0 / len(self._camera_headings)

        for i, heading_deg in enumerate(self._camera_headings):
            left = i * panel_fraction
            right = (i + 1) * panel_fraction

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

        if hasattr(builtins, "base"):
            delattr(builtins, "base")

        try:
            self.finalizeExit()
        except SystemExit:
            pass
