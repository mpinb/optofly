from src.utils.trigger_timing import TriggerTiming, extract_trigger_timing


def test_extract_trigger_timing_present_fields():
    result = extract_trigger_timing(
        {"braid_timestamp": 1.0, "handler_timestamp": 2.0, "other": "x"}
    )
    assert result == TriggerTiming(braid_timestamp=1.0, handler_timestamp=2.0)


def test_extract_trigger_timing_missing_fields():
    result = extract_trigger_timing({})
    assert result == TriggerTiming(braid_timestamp=None, handler_timestamp=None)


def test_extract_trigger_timing_partial_fields():
    result = extract_trigger_timing({"braid_timestamp": 5.0})
    assert result == TriggerTiming(braid_timestamp=5.0, handler_timestamp=None)
