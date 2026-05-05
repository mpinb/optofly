import math
import random

from panda3d.core import (
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    PNMImage,
    Texture,
    TextureStage,
)

from src.visual.base import BaseStimulus


def _generate_random_texture(
    width: int,
    height: int,
    square_size_px: int,
    density: float,
    fg_color: tuple,
    bg_color: tuple,
    seed: int,
) -> PNMImage:
    """Generate a random high-contrast square-pattern PNMImage."""
    if square_size_px <= 0:
        raise ValueError(f"square_size_px must be > 0, got {square_size_px}")
    if not 0.0 <= density <= 1.0:
        raise ValueError(f"density must be in [0.0, 1.0], got {density}")
    if len(fg_color) < 3 or len(bg_color) < 3:
        raise ValueError("Color tuples must have at least 3 elements (R, G, B)")
    rng = random.Random(seed)
    img = PNMImage(width, height)
    img.fill(bg_color[0] / 255.0, bg_color[1] / 255.0, bg_color[2] / 255.0)

    for row in range(0, height, square_size_px):
        for col in range(0, width, square_size_px):
            if rng.random() < density:
                for y in range(row, min(row + square_size_px, height)):
                    for x in range(col, min(col + square_size_px, width)):
                        img.setXel(
                            x, y,
                            fg_color[0] / 255.0,
                            fg_color[1] / 255.0,
                            fg_color[2] / 255.0,
                        )
    return img


def _build_cylinder_geom(
    radius: float,
    height: float,
    slices: int = 64,
    stacks: int = 8,
) -> GeomNode:
    """Build a cylinder centred at origin, axis along Z.

    The faces are wound so normals point inward (CCW from inside).
    """
    vformat = GeomVertexFormat.getV3n3c4t2()
    vdata = GeomVertexData("cylinder", vformat, Geom.UHStatic)
    vdata.setNumRows((slices + 1) * (stacks + 1))

    vertex = GeomVertexWriter(vdata, "vertex")
    normal = GeomVertexWriter(vdata, "normal")
    color = GeomVertexWriter(vdata, "color")
    texcoord = GeomVertexWriter(vdata, "texcoord")

    z_min = -height / 2.0
    z_step = height / stacks

    for s in range(stacks + 1):
        z = z_min + s * z_step
        v = s / stacks
        for i in range(slices + 1):
            angle = 2.0 * math.pi * i / slices
            # North = +Y (angle=0), East = +X (angle=90)
            x = radius * math.sin(angle)
            y = radius * math.cos(angle)
            u = i / slices

            vertex.addData3(x, y, z)
            # Inward-pointing normal
            normal.addData3(-math.sin(angle), -math.cos(angle), 0.0)
            color.addData4(1.0, 1.0, 1.0, 1.0)
            texcoord.addData2(u, v)

    tris = GeomTriangles(Geom.UHStatic)
    verts_per_row = slices + 1
    for s in range(stacks):
        base = s * verts_per_row
        for i in range(slices):
            # CCW from inside:
            # (base+i) is bottom-left
            # (base+i+1) is bottom-right
            # (base+i+verts_per_row+1) is top-right
            # (base+i+verts_per_row) is top-left
            a = base + i
            b = base + i + 1
            c = base + i + verts_per_row + 1
            d = base + i + verts_per_row
            
            # Triangle 1: a -> b -> c
            tris.addVertices(a, b, c)
            # Triangle 2: a -> c -> d
            tris.addVertices(a, c, d)
    tris.closePrimitive()

    geom = Geom(vdata)
    geom.addPrimitive(tris)
    node = GeomNode("cylinder")
    node.addGeom(geom)
    return node


class BackgroundStimulus(BaseStimulus):
    """Always-active background: textured cylinder surrounding the fly.

    The cylinder sits at origin, radius = viewing_distance_cm, and extends
    cylinder_height_cm vertically.  A seeded procedural square texture is
    wrapped and tiled around the inside.
    """

    def setup(self) -> None:
        cfg = self.config
        tex_img = _generate_random_texture(
            width=1920,
            height=1080,
            square_size_px=cfg.get("square_size_px", 40),
            density=cfg.get("density", 0.5),
            fg_color=tuple(cfg.get("foreground_color", [0, 0, 0])),
            bg_color=tuple(cfg.get("background_color", [255, 255, 255])),
            seed=cfg.get("seed", 42),
        )

        radius = self.scene.viewing_distance_cm
        height = cfg.get("cylinder_height_cm", 80)
        num_screens = cfg.get("num_screens", 4)

        # Create Cylinder
        cylinder_node = _build_cylinder_geom(radius, height)
        cylinder_np = self.scene.render.attachNewNode(cylinder_node)
        cylinder_np.setTwoSided(True) # Safety backup for culling
        
        cyl_tex = Texture("cylinder_tex")
        cyl_tex.load(tex_img)
        cyl_tex.setWrapU(Texture.WM_repeat)
        cyl_tex.setWrapV(Texture.WM_repeat)
        cylinder_np.setTexture(cyl_tex)

        # Physical tiling: tile_u = number of screens (4 panels wrap 360)
        circumference = 2.0 * math.pi * radius
        panel_width_cm = circumference / num_screens
        tile_u = float(num_screens)
        
        # Tile vertically to maintain square aspect ratio on the screen.
        # tile_v = Physical Height / (Physical width per texture repetition * texture aspect)
        tile_v = height / (panel_width_cm * 1080.0 / 1920.0)
        cylinder_np.setTexScale(TextureStage.getDefault(), tile_u, tile_v)

    def on_trigger(self, heading_deg: float, trigger_data: dict) -> None:
        pass

    def update(self, dt: float) -> None:
        pass
