from datetime import datetime

from src.utils.metadata import metadata_from_form


def _form(**overrides):
    base = {
        "experimenter": "",
        "cross": "",
        "cross_date": "",
        "f1_date": "",
        "atr_date": "",
        "experiment_date": "",
        "n_flies": "",
        "experiment_duration": "",
        "notes": "",
    }
    base.update(overrides)
    return base


def test_empty_form_uses_defaults():
    result = metadata_from_form(_form())
    assert result["experimenter"] == "N/A"
    assert result["experiment_date"] == datetime.now().strftime("%Y-%m-%d")
    assert result["n_flies"] == "N/A"
    assert result["experiment_duration"] == 24.0
    assert result["notes"] == "N/A"


def test_filled_form_is_used_verbatim():
    result = metadata_from_form(
        _form(experimenter="Jane", n_flies="12", experiment_duration="6.5", notes="test run")
    )
    assert result["experimenter"] == "Jane"
    assert result["n_flies"] == 12
    assert result["experiment_duration"] == 6.5
    assert result["notes"] == "test run"


def test_invalid_numeric_fields_fall_back_to_defaults():
    result = metadata_from_form(_form(n_flies="not-a-number", experiment_duration="also-bad"))
    assert result["n_flies"] == "N/A"
    assert result["experiment_duration"] == 24.0
