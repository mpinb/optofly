import random

from panda3d.core import (
    CardMaker,
    PNMImage,
    Point3,
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
    """Generate a random high-contrast square-pattern PNMImage.

    Args:
        width, height: Image size in pixels
        square_size_px: Size of each square tile in pixels
        density: Fraction of tiles filled with fg_color (0.0-1.0)
        fg_color: Foreground RGB (0-255 per channel)
        bg_color: Background RGB (0-255 per channel)
        seed: RNG seed for reproducibility

    Returns:
        PNMImage ready for Texture.load()
    """
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


class BackgroundStimulus(BaseStimulus):
    """Always-active background: 4 textured wall planes + optional ground plane.

    Walls are flat CardMaker quads at +-viewing_distance_cm on X and Y axes,
    each textured inward with a seeded random high-contrast pattern. The
    ground plane is a 500x500 cm horizontal quad at configurable Z height
    using the same texture tiled to match the wall square density.
    """

    # Physical width of one 1920 px screen panel (cm) -- used for UV tiling
    PANEL_WIDTH_CM = 52.7

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

        wall_tex = Texture("wall_tex")
        wall_tex.load(tex_img)

        d = self.scene.viewing_distance_cm
        hw = self.PANEL_WIDTH_CM / 2.0
        # Panel height in cm from aspect ratio
        hh = hw * (1080.0 / 1920.0)

        wall_positions = [
            ("wall_north", Point3(0, d, 0)),
            ("wall_east",  Point3(d, 0, 0)),
            ("wall_south", Point3(0, -d, 0)),
            ("wall_west",  Point3(-d, 0, 0)),
        ]

        for name, pos in wall_positions:
            cm = CardMaker(name)
            cm.setFrame(-hw, hw, -hh, hh)
            wall_np = self.scene.render.attachNewNode(cm.generate())
            wall_np.setPos(pos)
            wall_np.lookAt(Point3(0, 0, 0))
            wall_np.setTwoSided(True)
            wall_np.setTexture(wall_tex)

        if cfg.get("ground_enabled", True):
            self._setup_ground(tex_img, cfg.get("ground_z_cm", -5.0))

    def _setup_ground(self, tex_img: PNMImage, ground_z_cm: float) -> None:
        ground_extent = 500.0  # cm
        tile_count = ground_extent / self.PANEL_WIDTH_CM

        cm = CardMaker("ground")
        half = ground_extent / 2.0
        cm.setFrame(-half, half, -half, half)
        ground_np = self.scene.render.attachNewNode(cm.generate())
        ground_np.setPos(0, 0, ground_z_cm)
        ground_np.setP(-90)  # rotate CardMaker's XZ quad to lie horizontal
        ground_np.setTwoSided(True)

        ground_tex = Texture("ground_tex")
        ground_tex.load(tex_img)
        ground_tex.setWrapU(Texture.WM_repeat)
        ground_tex.setWrapV(Texture.WM_repeat)
        ground_np.setTexture(ground_tex)
        ground_np.setTexScale(TextureStage.getDefault(), tile_count, tile_count)

    def on_trigger(self, heading_deg: float, trigger_data: dict) -> None:
        pass  # static background, no trigger response

    def update(self, dt: float) -> None:
        pass  # static
