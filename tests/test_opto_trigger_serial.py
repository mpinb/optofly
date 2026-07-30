import random
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


class _FakeSerialForTrigger:
    def __init__(self):
        self.written = []
        self.in_waiting = 0

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def read(self, n):
        return b""


def _make_trigger_for_write_test(sham_probability=0.0):
    trigger = object.__new__(OptoTrigger)
    trigger.is_initialized = True
    trigger.serial_conn = _FakeSerialForTrigger()
    trigger.config = type(
        "Config",
        (),
        {
            "sham_probability": sham_probability,
            "color": "white",
            "duration": 100,
            "intensity": 255,
            "frequency": 0,
            "get_trigger_command": lambda self: "<100,255,0,white>",
        },
    )()
    trigger.logger = type(
        "Logger",
        (),
        {
            "info": lambda *a, **k: None,
            "debug": lambda *a, **k: None,
            "warning": lambda *a, **k: None,
            "error": lambda *a, **k: None,
        },
    )()
    trigger._parameter_combinations = [(100, 255, 0)]
    trigger.combination_counts = {(100, 255, 0): 0}
    return trigger


def test_trigger_returns_activation_timestamp_immediately_after_serial_write(
    monkeypatch,
):
    trigger = _make_trigger_for_write_test()
    monkeypatch.setattr(random, "random", lambda: 1.0)  # never sham
    monkeypatch.setattr("src.hardware.led.time.time", lambda: 42.0)

    success, was_sham, activation_timestamp = trigger.trigger(sham=None)

    assert success is True
    assert was_sham is False
    assert activation_timestamp == 42.0


def test_trigger_returns_none_activation_timestamp_for_sham():
    trigger = _make_trigger_for_write_test()

    success, was_sham, activation_timestamp = trigger.trigger(sham=True)

    assert success is True
    assert was_sham is True
    assert activation_timestamp is None


def test_trigger_returns_none_activation_timestamp_when_not_initialized():
    trigger = object.__new__(OptoTrigger)
    trigger.is_initialized = False
    trigger.logger = type("Logger", (), {"error": lambda *a, **k: None})()

    success, was_sham, activation_timestamp = trigger.trigger(sham=None)

    assert success is False
    assert activation_timestamp is None
