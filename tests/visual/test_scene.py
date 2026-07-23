# Requires a display. Run with: pytest -m display
import pytest


@pytest.mark.display
def test_arena_scene_creates_four_cameras():
    from src.visual.scene import ArenaScene

    scene = ArenaScene(standalone=True)
    assert len(scene.cameras) == 4
    scene.cleanup()


@pytest.mark.display
def test_arena_scene_standalone_window_size():
    from src.visual.scene import ArenaScene

    scene = ArenaScene(standalone=True)
    assert scene.win.getXSize() == 1280
    assert scene.win.getYSize() == 320
    scene.cleanup()
