import os

import pytest

# Interactive test scripts that require a display — skip in headless/CI environments
collect_ignore = []

if not os.environ.get("DISPLAY"):
    collect_ignore.append("test_looming_edge_wrapping.py")


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.display tests when no DISPLAY is set.

    Panda3D can partially initialize headlessly (some display-marked tests
    pass without one), but window-sizing behavior differs enough that at
    least one fails outright -- skip the whole marker rather than track
    which specific assertions are display-dependent.
    """
    if os.environ.get("DISPLAY"):
        return
    skip_display = pytest.mark.skip(reason="requires a display (DISPLAY not set)")
    for item in items:
        if "display" in item.keywords:
            item.add_marker(skip_display)
