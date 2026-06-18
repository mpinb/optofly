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
    """Panda3D window with 4 perspective cameras for a 360 panoramic fly arena.

    One 7680x1080 window (or 1280x320 in standalone) is split into four 1920x1080
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
            width, height = 1280, 320
            window_x_offset = 0
        else:
            width, height = 7680, 1080

        # Store dimensions before ShowBase.__init__ so _setup_cameras can use
        # them without querying the window (requestProperties is async and
        # unreliable for position on Linux).
        self._width = width
        self._height = height

        prc = f"win-size {width} {height}\n"
        if standalone:
            prc += "win-origin 0 0\n"
        else:
            prc += (
                f"win-origin {window_x_offset} 0\n"
                "undecorated true\n"
                "cursor-hidden true\n"
            )
        load_prc_file_data("", prc)

        ShowBase.__init__(self)
        self.disableMouse()

        self._panel_width = width // 4
        self._panel_height = height

        self.cameras: list[NodePath] = []
        self._setup_cameras()

    def _setup_cameras(self) -> None:
        """Create 4 perspective cameras, each assigned to a display region."""
        # Disable every display region ShowBase created (main 3D view + render2d
        # overlay). Leaving any full-window region active would overdraw our panels.
        for i in range(self.win.getNumDisplayRegions()):
            self.win.getDisplayRegion(i).setActive(False)

        panel_fraction = 1.0 / len(self._camera_headings)
        panel_width = self._width / len(self._camera_headings)
        win_height = self._height

        for i, heading_deg in enumerate(self._camera_headings):
            left = i * panel_fraction
            right = (i + 1) * panel_fraction

            region = self.win.makeDisplayRegion(left, right, 0.0, 1.0)
            region.setClearColorActive(True)
            region.setClearColor((0.5, 0.5, 0.5, 1))
            region.setClearDepthActive(True)

            cam_node = Camera(f"arena_cam_{i}")
            lens = PerspectiveLens()
            lens.setFov(90.0)
            lens.setAspectRatio(panel_width / win_height)
            lens.setNearFar(0.1, 5000.0)
            cam_node.setLens(lens)

            cam_np = self.render.attachNewNode(cam_node)
            cam_np.setPos(0, 0, 0)
            # Compass heading is Clockwise (N=0, E=90).
            # Panda3D H is Counter-Clockwise (N=0, W=90).
            # So H_panda = -H_compass.
            cam_np.setH(-heading_deg)

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
