import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.visual.scene import ArenaScene
    from panda3d.core import NodePath


def angular_to_world_pos(
    heading_deg: float, elevation_deg: float, distance_cm: float
) -> tuple[float, float, float]:
    """Convert angular position (degrees) to Panda3D world coordinates (cm).

    World convention: North=+Y, East=+X, Z=up, fly at origin.

    Args:
        heading_deg: Compass bearing (0=North, 90=East, 180=South, 270=West)
        elevation_deg: Angle above (+) or below (-) horizon
        distance_cm: Radial distance from origin to disk center

    Returns:
        (x, y, z) in centimeters
    """
    h_rad = math.radians(heading_deg)
    e_rad = math.radians(elevation_deg)
    horiz = distance_cm * math.cos(e_rad)
    x = horiz * math.sin(h_rad)
    y = horiz * math.cos(h_rad)
    z = distance_cm * math.sin(e_rad)
    return x, y, z


def angular_size_to_radius(size_deg: float, distance_cm: float) -> float:
    """Convert full angular diameter (degrees) to physical radius (cm).

    For a billboard disk always facing the viewer:
        visual_angle = 2 * atan(R / D)  ->  R = tan(size_deg / 2) * D

    Args:
        size_deg: Full angular diameter in degrees
        distance_cm: Distance from origin to disk center

    Returns:
        Physical radius in centimeters
    """
    return math.tan(math.radians(size_deg / 2.0)) * distance_cm


def _make_unit_disk(color: tuple, num_segments: int = 32):
    """Create a flat disk (radius=1) in the local XZ plane as a GeomNode.

    Scale the returned NodePath to set the actual radius (1 unit = 1 cm).
    Color values are 0-255 integers.
    """
    from panda3d.core import (
        GeomVertexFormat,
        GeomVertexData,
        GeomVertexWriter,
        Geom,
        GeomTriangles,
        GeomNode,
    )

    if num_segments < 3:
        raise ValueError(f"num_segments must be >= 3, got {num_segments}")

    r, g, b = color[0] / 255.0, color[1] / 255.0, color[2] / 255.0
    a = color[3] / 255.0 if len(color) == 4 else 1.0

    vformat = GeomVertexFormat.getV3c4()
    vdata = GeomVertexData("disk", vformat, Geom.UHStatic)
    vdata.setNumRows(num_segments + 1)

    vertex = GeomVertexWriter(vdata, "vertex")
    color_w = GeomVertexWriter(vdata, "color")

    # Center vertex (index 0)
    vertex.addData3(0, 0, 0)
    color_w.addData4(r, g, b, a)

    # Edge vertices in XZ plane (indices 1..num_segments)
    for i in range(num_segments):
        angle = 2.0 * math.pi * i / num_segments
        vertex.addData3(math.cos(angle), 0.0, math.sin(angle))
        color_w.addData4(r, g, b, a)

    tris = GeomTriangles(Geom.UHStatic)
    for i in range(num_segments):
        v1 = i + 1
        v2 = (i + 1) % num_segments + 1
        tris.addVertices(0, v1, v2)
    tris.closePrimitive()

    geom = Geom(vdata)
    geom.addPrimitive(tris)

    node = GeomNode("disk")
    node.addGeom(geom)
    return node


class BaseStimulus(ABC):
    """Abstract base for Panda3D stimuli.

    Subclasses implement setup / on_trigger / update and use the angular helper
    methods (add_disk, set_angular_size, remove_node). They never interact with
    Panda3D APIs directly.
    """

    def __init__(self, config: dict, scene: "ArenaScene"):
        self.config = config
        self.scene = scene

    @abstractmethod
    def setup(self) -> None:
        """Called once at startup. Create scene nodes using helper methods."""

    @abstractmethod
    def on_trigger(self, heading_deg: float, trigger_data: dict) -> dict | None:
        """Called on ZONE_ENTER. heading_deg is Braid->world converted (degrees).

        Return a dict of stimulus parameters to log (merged into stim.csv row),
        or None if nothing was logged / stimulus was inactive.
        """

    @abstractmethod
    def update(self, dt: float) -> None:
        """Called every frame. Animate stimulus here. dt is elapsed time in seconds."""

    # -- Angular API helpers ----------------------------------------------------

    def add_disk(
        self,
        heading_deg: float,
        size_deg: float,
        elevation_deg: float = 0.0,
        color: tuple = (0, 0, 0),
        distance_cm: float | None = None,
    ) -> "NodePath":
        """Place a billboard disk at the given heading and elevation.

        The disk always faces the fly (at origin), so it appears as a perfect
        circle in the visual field regardless of which screen panel it falls on.

        Args:
            heading_deg: Compass bearing (0=North, 90=East)
            size_deg: Full angular diameter in degrees
            elevation_deg: Angle above (+) or below (-) horizon
            color: RGB tuple (values 0-255)

        Returns:
            NodePath -- pass to set_angular_size() or remove_node()
        """
        # Default: 1 cm inside the cylinder. Callers with large disks should
        # pass distance_cm explicitly (see LoomingStimulus for the formula).
        disk_dist = (
            distance_cm
            if distance_cm is not None
            else self.scene.viewing_distance_cm - 1.0
        )
        x, y, z = angular_to_world_pos(heading_deg, elevation_deg, disk_dist)
        radius = angular_size_to_radius(size_deg, disk_dist)

        disk_geom = _make_unit_disk(color)
        disk_np = self.scene.render.attachNewNode(disk_geom)
        disk_np.setPos(x, y, z)
        disk_np.setScale(radius)
        disk_np.setBillboardPointWorld()
        disk_np.setTwoSided(True)
        return disk_np

    def set_angular_size(self, node: "NodePath", size_deg: float) -> None:
        """Update an existing disk's angular size in-place."""
        pos = node.getPos()
        dist = pos.length()
        if dist < 0.01:
            return
        radius = angular_size_to_radius(size_deg, dist)
        node.setScale(radius)

    def remove_node(self, node: "NodePath") -> None:
        """Remove a node from the scene graph."""
        if node and not node.isEmpty():
            node.removeNode()
