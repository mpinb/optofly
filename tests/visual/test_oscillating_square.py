import pytest

from src.visual.stimuli.oscillating_square import (
    OscillatingSquare,
    _make_unit_square,
)


class TestOscillatingSquareConfig:
    def test_default_config(self):
        stim = OscillatingSquare({}, scene=None)
        assert stim._size_deg == 10.0
        assert stim._amplitude_deg == 30.0
        assert stim._frequency_hz == 1.0
        assert stim._duration_ms == 2000.0
        assert stim._color == (0, 0, 0)
        assert stim._positions_deg == [-45, 0, 45]
        assert stim._seed == 42

    def test_custom_config(self):
        stim = OscillatingSquare(
            {
                "size_deg": 20.0,
                "amplitude_deg": 45.0,
                "frequency_hz": 5.0,
                "duration_ms": 500.0,
                "color": [255, 128, 64],
                "positions_deg": [0, 90],
                "seed": 99,
            },
            scene=None,
        )
        assert stim._size_deg == 20.0
        assert stim._amplitude_deg == 45.0
        assert stim._frequency_hz == 5.0
        assert stim._duration_ms == 500.0
        assert stim._color == (255, 128, 64)
        assert stim._positions_deg == [0, 90]
        assert stim._seed == 99

    def test_state_starts_idle(self):
        stim = OscillatingSquare({}, scene=None)
        assert stim._state == OscillatingSquare.IDLE


class TestMakeUnitSquare:
    def test_creates_geomnode(self):
        node = _make_unit_square((255, 0, 0))
        assert node is not None
        assert node.getName() == "square"
        assert node.getNumGeoms() == 1


class TestOscillatingSquareState:
    def test_double_trigger_ignored(self):
        stim = OscillatingSquare({"positions_deg": [0]}, scene=None)
        stim._state = OscillatingSquare.ACTIVE
        stim._base_heading = 90.0
        stim._offset_deg = 45.0
        stim.on_trigger(0.0, {})
        # Should have ignored re-trigger — values unchanged
        assert stim._base_heading == 90.0
        assert stim._offset_deg == 45.0

    def test_update_idle_noop(self):
        stim = OscillatingSquare({}, scene=None)
        # update() on IDLE state should do nothing (no crash)
        stim.update(0.016)


@pytest.mark.display
class TestOscillatingSquareDisplay:
    def test_setup_and_trigger_cycle(self):
        from src.visual.scene import ArenaScene

        scene = ArenaScene(standalone=True)
        stim = OscillatingSquare(
            {
                "size_deg": 10.0,
                "amplitude_deg": 30.0,
                "frequency_hz": 2.0,
                "duration_ms": 2000.0,
                "color": [0, 0, 0],
                "positions_deg": [0],
                "seed": 42,
            },
            scene,
        )
        stim.setup()
        assert stim._square is not None
        assert stim._square.isStashed()

        # Trigger at North heading
        stim.on_trigger(0.0, {"obj_id": 1})
        assert stim._state == OscillatingSquare.ACTIVE
        assert not stim._square.isStashed()

        # After 2000ms of updates, should return to IDLE
        for _ in range(80):
            stim.update(0.025)
        assert stim._state == OscillatingSquare.IDLE
        assert stim._square.isStashed()

        scene.cleanup()

    def test_position_changes_during_oscillation(self):
        from src.visual.scene import ArenaScene

        scene = ArenaScene(standalone=True)
        stim = OscillatingSquare(
            {
                "size_deg": 10.0,
                "amplitude_deg": 30.0,
                "frequency_hz": 2.0,
                "duration_ms": 2000.0,
                "color": [0, 0, 0],
                "positions_deg": [0],
                "seed": 42,
            },
            scene,
        )
        stim.setup()
        stim.on_trigger(0.0, {"obj_id": 1})

        pos1 = stim._square.getPos()
        stim.update(0.125)  # Quarter period at 2Hz = half oscillation
        pos2 = stim._square.getPos()

        assert pos1 != pos2

        scene.cleanup()
