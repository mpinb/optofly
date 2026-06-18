import pytest

from src.visual.stimuli.background import _generate_random_texture


def test_texture_dimensions():
    img = _generate_random_texture(
        width=192,
        height=108,
        square_size_px=20,
        density=0.5,
        fg_color=(0, 0, 0),
        bg_color=(255, 255, 255),
        seed=42,
    )
    assert img.getXSize() == 192
    assert img.getYSize() == 108


def test_texture_reproducible_with_same_seed():
    kwargs = dict(
        width=192,
        height=108,
        square_size_px=20,
        density=0.5,
        fg_color=(0, 0, 0),
        bg_color=(255, 255, 255),
        seed=42,
    )
    img1 = _generate_random_texture(**kwargs)
    img2 = _generate_random_texture(**kwargs)
    for x in range(0, 192, 16):
        for y in range(0, 108, 16):
            assert img1.getXelVal(x, y) == img2.getXelVal(x, y)


def test_texture_differs_with_different_seeds():
    img1 = _generate_random_texture(
        192, 108, 20, 0.5, (0, 0, 0), (255, 255, 255), seed=1
    )
    img2 = _generate_random_texture(
        192, 108, 20, 0.5, (0, 0, 0), (255, 255, 255), seed=2
    )
    diffs = sum(
        1
        for x in range(0, 192, 8)
        for y in range(0, 108, 8)
        if img1.getXelVal(x, y) != img2.getXelVal(x, y)
    )
    assert diffs > 0


def test_texture_density_zero_gives_all_background():
    img = _generate_random_texture(
        64, 64, 8, density=0.0, fg_color=(0, 0, 0), bg_color=(255, 255, 255), seed=0
    )
    for x in range(64):
        for y in range(64):
            assert img.getXelVal(x, y) == (255, 255, 255)


def test_texture_density_one_gives_all_foreground():
    img = _generate_random_texture(
        64, 64, 8, density=1.0, fg_color=(0, 0, 0), bg_color=(255, 255, 255), seed=0
    )
    for x in range(64):
        for y in range(64):
            assert img.getXelVal(x, y) == (0, 0, 0)


@pytest.mark.display
def test_background_cylinder_setup_does_not_raise():
    from src.visual.scene import ArenaScene
    from src.visual.stimuli.background import BackgroundStimulus

    scene = ArenaScene(standalone=True)
    stimulus = BackgroundStimulus(
        {
            "square_size_px": 40,
            "density": 0.5,
            "foreground_color": [0, 0, 0],
            "background_color": [255, 255, 255],
            "seed": 42,
        },
        scene,
    )
    stimulus.setup()
    assert not scene.render.getChildren().isEmpty()
    scene.cleanup()
