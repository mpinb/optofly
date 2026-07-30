"""LiquidLens error logging must survive a persistent fault.

Both of the loop's exception handlers sit inside a `while` that polls with a
10 ms timeout, so a fault that doesn't clear -- unplugged serial cable, lens
firmware wedged -- logs one line per iteration for the remainder of the run.
Over a 24-hour experiment that is millions of identical lines written into
optofly.log in the braid folder, which buries every other diagnostic and can
fill the disk the recordings are going to.
"""

from src.processes.lens import LiquidLens


class RecordingLogger:
    def __init__(self):
        self.errors = []

    def error(self, msg, *args):
        self.errors.append(msg % args if args else msg)

    def exception(self, msg, *args):
        self.errors.append(msg % args if args else msg)

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


def _bare_lens():
    lens = object.__new__(LiquidLens)
    lens.logger = RecordingLogger()
    lens._last_error_message = None
    lens._suppressed_error_count = 0
    return lens


def test_repeated_identical_errors_are_logged_once():
    lens = _bare_lens()

    for _ in range(500):
        lens._log_error_throttled("Error adjusting lens: device disconnected")

    assert len(lens.logger.errors) == 1
    assert "device disconnected" in lens.logger.errors[0]


def test_a_new_error_flushes_the_suppressed_count_first():
    """The count matters: "repeated 499 more times" is the difference between
    a transient glitch and a dead lens."""
    lens = _bare_lens()

    for _ in range(500):
        lens._log_error_throttled("Error adjusting lens: device disconnected")
    lens._log_error_throttled("Error adjusting lens: value out of range")

    assert len(lens.logger.errors) == 3
    assert "device disconnected" in lens.logger.errors[0]
    assert "499" in lens.logger.errors[1]
    assert "repeated" in lens.logger.errors[1].lower()
    assert "value out of range" in lens.logger.errors[2]


def test_alternating_errors_are_both_reported():
    """Throttling must not hide a genuinely changing fault."""
    lens = _bare_lens()

    lens._log_error_throttled("first")
    lens._log_error_throttled("second")
    lens._log_error_throttled("first")

    assert [e for e in lens.logger.errors if e in ("first", "second")] == [
        "first",
        "second",
        "first",
    ]


def test_distinct_errors_are_never_suppressed():
    lens = _bare_lens()

    for i in range(10):
        lens._log_error_throttled(f"error number {i}")

    assert len(lens.logger.errors) == 10


def test_flush_reports_a_trailing_suppressed_run():
    """A fault that persists to shutdown must still have its count recorded,
    or the log ends mid-suppression showing a single error."""
    lens = _bare_lens()

    for _ in range(42):
        lens._log_error_throttled("stuck")
    lens._flush_suppressed_errors()

    assert "41" in lens.logger.errors[-1]
