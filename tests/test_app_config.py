from src.utils.config import AppConfig


def test_load_builds_the_whole_tree():
    app = AppConfig.load("configs/config.example.toml")
    assert app.camera.fps > 0
    assert app.trigger_handler.zone_timeout > 0
    assert app.liquid_lens.port
    assert app.zmq.braid_port > 0
    assert app.braid_publisher.host
    assert app.opto_trigger.port
    assert app.logging.level
    assert isinstance(app.visual_stimuli.active, bool)


def test_shared_subconfigs_are_the_same_instance_not_reparsed():
    """trigger_handler.zmq and braid_publisher.zmq must be the SAME object
    AppConfig built once, not two separately-parsed copies -- proves the
    single-parse, no-cross-construction property end to end."""
    app = AppConfig.load("configs/config.example.toml")
    assert app.trigger_handler.zmq is app.zmq
    assert app.braid_publisher.zmq is app.zmq
    assert app.liquid_lens.zmq is app.zmq
    assert app.liquid_lens.zone_timeout == app.trigger_handler.zone_timeout


def test_toml_file_parsed_exactly_once(monkeypatch):
    import tomllib

    from src.utils import config as config_module

    config_module._TOML_CACHE.clear()
    call_count = {"n": 0}
    real_load = tomllib.load

    def counting_load(f):
        call_count["n"] += 1
        return real_load(f)

    monkeypatch.setattr(config_module.tomllib, "load", counting_load)

    AppConfig.load("configs/config.example.toml")

    assert call_count["n"] == 1


def test_frozen_instance_cannot_be_mutated():
    import pytest

    app = AppConfig.load("configs/config.example.toml")
    with pytest.raises(Exception):
        app.camera = None
