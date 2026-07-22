from datetime import datetime
from unittest.mock import patch, MagicMock

from src.utils.metadata import metadata_from_form, collect_metadata


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


def test_experiment_duration_valid_24_parsed_correctly():
    """Valid "24" input should parse to 24.0, not trigger coercion to default."""
    result = metadata_from_form(_form(experiment_duration="24"))
    assert result["experiment_duration"] == 24.0


def test_experiment_duration_valid_24_0_parsed_correctly():
    """Valid "24.0" input should parse to 24.0, not trigger coercion to default."""
    result = metadata_from_form(_form(experiment_duration="24.0"))
    assert result["experiment_duration"] == 24.0


@patch("builtins.input")
@patch("builtins.print")
def test_collect_metadata_warning_only_on_invalid_duration(mock_print, mock_input):
    """
    Test that collect_metadata() only prints the warning when parse actually fails,
    not when the user types a valid "24".

    This catches the regression where the warning condition was value-based (== 24.0)
    instead of success-based, which would print a false warning for valid "24" input.
    """
    # Simulate user typing "24" for experiment_duration (and defaults for other fields)
    mock_input.side_effect = [
        "",  # experimenter
        "",  # cross
        "",  # cross_date
        "",  # f1_date
        "",  # atr_date
        "",  # experiment_date
        "",  # n_flies
        "24",  # experiment_duration - valid input
        "",  # notes
        "y",  # confirm
    ]

    result = collect_metadata()

    # Verify that 24.0 was stored correctly
    assert result["experiment_duration"] == 24.0

    # Verify that the warning was NOT printed for valid "24" input
    # (Check that none of the print calls contain "Invalid number")
    warning_printed = any(
        "Invalid number" in str(call) for call in mock_print.call_args_list
    )
    assert not warning_printed, "Warning should not be printed for valid '24' input"


@patch("builtins.input")
@patch("builtins.print")
def test_collect_metadata_warning_on_invalid_duration(mock_print, mock_input):
    """
    Test that collect_metadata() DOES print the warning when parse actually fails.
    """
    # Simulate user typing "abc" for experiment_duration (and defaults for other fields)
    mock_input.side_effect = [
        "",  # experimenter
        "",  # cross
        "",  # cross_date
        "",  # f1_date
        "",  # atr_date
        "",  # experiment_date
        "",  # n_flies
        "abc",  # experiment_duration - INVALID input
        "",  # notes
        "y",  # confirm
    ]

    result = collect_metadata()

    # Verify that 24.0 (default) was stored
    assert result["experiment_duration"] == 24.0

    # Verify that the warning WAS printed for invalid input
    warning_printed = any(
        "Invalid number" in str(call) for call in mock_print.call_args_list
    )
    assert warning_printed, "Warning should be printed for invalid input"
