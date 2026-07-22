import shutil

import pytest

from src.gui.config_editor import (
    CONFIG_TOML_FIELDS,
    VISUAL_STIMULI_TOML_FIELDS,
    coerce_form_value,
    load_fields,
    save_fields,
)


@pytest.fixture
def config_copy(tmp_path):
    dest = tmp_path / "config.toml"
    shutil.copy("configs/config.example.toml", dest)
    return dest


@pytest.fixture
def visual_stimuli_copy(tmp_path):
    dest = tmp_path / "visual_stimuli.toml"
    shutil.copy("configs/visual_stimuli.example.toml", dest)
    return dest


def test_load_fields_reads_known_values(config_copy):
    values = load_fields(str(config_copy), CONFIG_TOML_FIELDS)
    assert values["opto_trigger.active"] is False
    assert values["opto_trigger.color"] == "red"
    assert values["camera.active"] is True


def test_save_fields_updates_only_named_keys_and_preserves_comments(config_copy):
    original_text = config_copy.read_text()
    assert "# LED optogenetic stimulation" in original_text

    save_fields(
        str(config_copy),
        CONFIG_TOML_FIELDS,
        {"opto_trigger.active": True, "opto_trigger.color": "blue"},
    )

    new_text = config_copy.read_text()
    assert "# LED optogenetic stimulation" in new_text  # comment preserved
    assert "port = \"/dev/opto_trigger\"" in new_text  # untouched key preserved

    values = load_fields(str(config_copy), CONFIG_TOML_FIELDS)
    assert values["opto_trigger.active"] is True
    assert values["opto_trigger.color"] == "blue"
    assert values["camera.active"] is True  # untouched


def test_save_fields_rejects_unknown_field(config_copy):
    with pytest.raises(ValueError):
        save_fields(str(config_copy), CONFIG_TOML_FIELDS, {"opto_trigger.port": "/dev/tty0"})


def test_visual_stimuli_fields_round_trip(visual_stimuli_copy):
    values = load_fields(str(visual_stimuli_copy), VISUAL_STIMULI_TOML_FIELDS)
    assert values["visual_stimuli.looming.enabled"] is True

    save_fields(
        str(visual_stimuli_copy),
        VISUAL_STIMULI_TOML_FIELDS,
        {"visual_stimuli.looming.enabled": False},
    )
    values = load_fields(str(visual_stimuli_copy), VISUAL_STIMULI_TOML_FIELDS)
    assert values["visual_stimuli.looming.enabled"] is False


@pytest.mark.parametrize(
    "raw,field_type,expected",
    [
        ("on", bool, True),
        (None, bool, False),
        ("0.5", float, 0.5),
        ("red", str, "red"),
        ("100,200,300", list, [100, 200, 300]),
        ("150", list, [150]),
    ],
)
def test_coerce_form_value(raw, field_type, expected):
    assert coerce_form_value(raw, field_type) == expected
