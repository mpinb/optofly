import math
import random

from src.visual.base import BaseStimulus, angular_to_world_pos, angular_size_to_radius


def _make_unit_square(color: tuple):
    """Create a flat square (half-side=1) in the local XZ plane as a GeomNode.

    Scale the returned NodePath to set the actual half-side (1 unit = 1 cm).
    Color values are 0-255 integers.
    """
    from panda3d.core import (
        Geom,
        GeomNode,
        GeomTriangles,
        GeomVertexData,
        GeomVertexFormat,
        GeomVertexWriter,
    )

    r, g, b = color[0] / 255.0, color[1] / 255.0, color[2] / 255.0
    a = color[3] / 255.0 if len(color) == 4 else 1.0

    vformat = GeomVertexFormat.getV3c4()
    vdata = GeomVertexData("square", vformat, Geom.UHStatic)
    vdata.setNumRows(4)

    vertex = GeomVertexWriter(vdata, "vertex")
    color_w = GeomVertexWriter(vdata, "color")

    # Four corners in XZ plane, half-side = 1
    vertex.addData3(1, 0, 1)
    color_w.addData4(r, g, b, a)
    vertex.addData3(1, 0, -1)
    color_w.addData4(r, g, b, a)
    vertex.addData3(-1, 0, -1)
    color_w.addData4(r, g, b, a)
    vertex.addData3(-1, 0, 1)
    color_w.addData4(r, g, b, a)

    tris = GeomTriangles(Geom.UHStatic)
    tris.addVertices(0, 1, 2)
    tris.addVertices(0, 2, 3)
    tris.closePrimitive()

    geom = Geom(vdata)
    geom.addPrimitive(tris)

    node = GeomNode("square")
    node.addGeom(geom)
    return node


class OscillatingSquare(BaseStimulus):
    """A square billboard that oscillates left/right in front of the fly.

    On ZONE_ENTER, the square appears at fly_heading + random(positions_deg)
    and oscillates sinusoidally for duration_ms, then disappears.
    """

    IDLE = 0
    ACTIVE = 1

    def __init__(self, config: dict, scene):
        super().__init__(config, scene)
        self._size_deg: float = config.get("size_deg", 10.0)
        self._amplitude_deg: float = config.get("amplitude_deg", 30.0)
        self._frequency_hz: float = config.get("frequency_hz", 1.0)
        self._duration_ms: float = config.get("duration_ms", 2000.0)
        self._color: tuple = tuple(config.get("color", [0, 0, 0]))
        self._positions_deg: list = config.get("positions_deg", [-45, 0, 45])
        self._seed: int = config.get("seed", 42)
        self._rng = random.Random(self._seed)
        self._state = self.IDLE
        self._square = None
        self._elapsed_ms: float = 0.0
        self._base_heading: float = 0.0
        self._offset_deg: float = 0.0

    def setup(self) -> None:
        square_geom = _make_unit_square(self._color)
        self._square = self.scene.render.attachNewNode(square_geom)
        self._square.setBillboardPointWorld()
        self._square.setTwoSided(True)
        self._square.stash()

    def on_trigger(self, heading_deg: float, trigger_data: dict) -> dict | None:
        if self._state != self.IDLE:
            return None
        self._state = self.ACTIVE
        self._base_heading = heading_deg
        self._offset_deg = self._rng.choice(self._positions_deg)
        self._elapsed_ms = 0.0
        self._place_square(heading_deg + self._offset_deg)
        return {
            "square_stimulus_heading_deg": heading_deg + self._offset_deg,
            "square_offset_deg": self._offset_deg,
            "square_size_deg": self._size_deg,
            "square_amplitude_deg": self._amplitude_deg,
            "square_frequency_hz": self._frequency_hz,
            "square_duration_ms": self._duration_ms,
        }

    def update(self, dt: float) -> None:
        if self._state != self.ACTIVE:
            return

        self._elapsed_ms += dt * 1000.0

        if self._elapsed_ms >= self._duration_ms:
            self._square.stash()
            self._state = self.IDLE
            return

        t_sec = self._elapsed_ms / 1000.0
        oscillation = (
            math.sin(t_sec * self._frequency_hz * 2.0 * math.pi) * self._amplitude_deg
        )
        current_heading = self._base_heading + self._offset_deg + oscillation
        self._place_square(current_heading)

    def _place_square(self, heading_deg: float) -> None:
        dist = self.scene.viewing_distance_cm - 1.0
        x, y, z = angular_to_world_pos(heading_deg, 0.0, dist)
        radius = angular_size_to_radius(self._size_deg, dist)
        self._square.setPos(x, y, z)
        self._square.setScale(radius)
        self._square.unstash()
