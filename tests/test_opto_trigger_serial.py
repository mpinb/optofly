import time

from src.hardware.led import OptoTrigger


class _FakeSerial:
    """Delivers `chunks` at fixed real-time offsets from construction, then
    goes quiet -- mimics an Arduino that responds within a few ms.

    `read()` drains whatever it returns, same as pyserial: once consumed,
    those bytes no longer count toward `in_waiting`. (Without draining,
    `in_waiting` would stay truthy forever after the first chunk "arrives",
    and `_collect_serial_output`'s `continue`-on-data branch would spin
    forever re-reading the same bytes instead of ever reaching its
    timeout/quiet checks.)
    """

    def __init__(self, chunks_with_delays):
        self._chunks_with_delays = list(chunks_with_delays)
        self._start = time.time()
        self._delivered = 0
        self._consumed = 0

    @property
    def in_waiting(self):
        elapsed = time.time() - self._start
        while (
            self._delivered < len(self._chunks_with_delays)
            and self._chunks_with_delays[self._delivered][1] <= elapsed
        ):
            self._delivered += 1
        return self._delivered - self._consumed

    def read(self, n):
        # `n` here is our own in_waiting count, not bytes -- treat each
        # "waiting" unit as "one more queued chunk is ready to read".
        end = min(self._consumed + n, self._delivered)
        chunk = b"".join(
            self._chunks_with_delays[i][0] for i in range(self._consumed, end)
        )
        self._consumed = end
        return chunk


def _make_trigger():
    trigger = object.__new__(OptoTrigger)
    return trigger


def test_returns_promptly_once_a_complete_line_arrives_and_goes_quiet():
    """The Arduino responds once, quickly -- _collect_serial_output must
    not block the full 1s timeout waiting it out."""
    trigger = _make_trigger()
    trigger.serial_conn = _FakeSerial([(b"OK\n", 0.02)])

    start = time.time()
    lines = trigger._collect_serial_output(timeout=1.0, poll_interval=0.05)
    elapsed = time.time() - start

    assert lines == ["OK"]
    assert elapsed < 0.3  # well under the 1s timeout


def test_falls_back_to_full_timeout_when_arduino_never_responds():
    trigger = _make_trigger()
    trigger.serial_conn = _FakeSerial([])

    start = time.time()
    lines = trigger._collect_serial_output(timeout=0.2, poll_interval=0.05)
    elapsed = time.time() - start

    assert lines == []
    assert elapsed >= 0.2
