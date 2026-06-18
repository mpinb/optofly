import builtins
import pytest


@pytest.fixture(autouse=True)
def cleanup_showbase():
    """Ensure builtins.base is cleaned up before each test."""
    # Before test
    if hasattr(builtins, "base"):
        try:
            builtins.base.cleanup()
        except Exception:
            pass
        if hasattr(builtins, "base"):
            delattr(builtins, "base")

    yield

    # After test
    if hasattr(builtins, "base"):
        try:
            builtins.base.cleanup()
        except Exception:
            pass
        if hasattr(builtins, "base"):
            delattr(builtins, "base")
