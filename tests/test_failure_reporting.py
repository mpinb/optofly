"""A dying child process must report why it died, not let the parent guess.

_CRITICAL_INIT_HINTS maps a process name to one fixed sentence, so a LiquidLens
that died on a missing calibrations/liquid_lens.csv was reported as "Check
hardware connection and the relevant port in config.toml" -- sending the user
to unplug a working lens. The real exception was logged, but above the FATAL
line, so the prominent last word was the wrong one.
"""

import multiprocessing as mp

import pytest

import src.orchestration as orchestration
from src.utils.worker import WorkerProcess


class ExplodingWorker(WorkerProcess):
    """Fails in _run() the way LiquidLens does on a bad calibration file."""

    def _run(self):
        raise RuntimeError("calibrations/liquid_lens.csv: expected columns z, dpt")


class QuietWorker(WorkerProcess):
    def _run(self):
        return


def test_worker_reports_its_exception_through_the_failure_queue():
    queue = mp.Queue()
    event = mp.Event()
    worker = ExplodingWorker(event=event, process_name="LiquidLens", failure_queue=queue)

    worker.start()
    worker.join(timeout=10)

    assert not queue.empty(), "a crashing worker must report why it died"
    name, reason = queue.get(timeout=5)
    assert name == "LiquidLens"
    assert "liquid_lens.csv" in reason
    assert "RuntimeError" in reason


def test_worker_that_exits_cleanly_reports_nothing():
    queue = mp.Queue()
    event = mp.Event()
    worker = QuietWorker(event=event, process_name="LiquidLens", failure_queue=queue)

    worker.start()
    worker.join(timeout=10)

    assert queue.empty()


def test_failure_queue_is_optional():
    """Standalone `python -m src.processes.tracking` builds workers without one."""
    event = mp.Event()
    worker = QuietWorker(event=event, process_name="Standalone")

    worker.start()
    worker.join(timeout=10)

    assert worker.exitcode == 0


class _FakeDeadProcess:
    def is_alive(self):
        return False


def test_reported_reason_replaces_the_static_hint():
    """The child's own message wins over the generic hardware hint."""
    processes = [("LiquidLens", _FakeDeadProcess())]
    reported = {"LiquidLens": "RuntimeError: calibrations/liquid_lens.csv: expected columns z, dpt"}

    messages = orchestration._check_critical_processes_alive(
        processes, {"LiquidLens"}, reported
    )

    assert len(messages) == 1
    assert "liquid_lens.csv" in messages[0]
    assert "hardware connection" not in messages[0], (
        f"must not misattribute a calibration-file failure to hardware: {messages[0]}"
    )


def test_static_hint_is_still_used_when_the_child_reported_nothing():
    """A process that exits without raising (OptoTriggerWorker's
    swallow-and-return path) leaves nothing on the queue."""
    processes = [("LiquidLens", _FakeDeadProcess())]

    messages = orchestration._check_critical_processes_alive(
        processes, {"LiquidLens"}, {}
    )

    assert len(messages) == 1
    assert "hardware" in messages[0].lower()


@pytest.mark.parametrize("reported", [None, {}])
def test_reported_reasons_argument_is_optional(reported):
    processes = [("BraidPublisher", _FakeDeadProcess())]

    messages = orchestration._check_critical_processes_alive(
        processes, {"BraidPublisher"}, reported
    )

    assert "Braid" in messages[0]
