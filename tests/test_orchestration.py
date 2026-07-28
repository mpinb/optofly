import pytest

import src.orchestration as orchestration
from src.orchestration import Experiment


class FakeProcess:
    """Stand-in for a WorkerProcess subclass -- never touches real hardware/ZMQ."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self._alive = False
        self.terminated = False
        FakeProcess.instances.append(self)

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self._alive = False

    def terminate(self):
        self.terminated = True
        self._alive = False


@pytest.fixture(autouse=True)
def patch_processes(monkeypatch, tmp_path):
    FakeProcess.instances = []
    for name in (
        "BraidPublisher",
        "TriggerHandler",
        "LatencyLogger",
        "VisualProcess",
        "OptoTriggerWorker",
        "CameraProcess",
        "LiquidLens",
    ):
        monkeypatch.setattr(orchestration, name, FakeProcess)
    monkeypatch.setattr(orchestration, "run_server", lambda *a, **kw: None)
    monkeypatch.setattr(orchestration.time, "sleep", lambda *_: None)

    braid_folder = tmp_path / "20260101_000000.braid"
    braid_folder.mkdir()
    monkeypatch.setattr(
        orchestration,
        "check_braid_folder_exists",
        lambda *a, **kw: (str(braid_folder), None),
    )
    return braid_folder


@pytest.fixture
def config_path():
    return "configs/config.example.toml"


def test_stop_before_start_is_a_no_op():
    exp = Experiment()
    exp.stop()  # must not raise
    assert exp.status()["running"] is False


def test_needs_cleanup_false_before_start():
    exp = Experiment()
    assert exp.needs_cleanup() is False


def test_is_running_false_before_start():
    exp = Experiment()
    assert exp.is_running() is False


def test_status_before_start_has_empty_processes():
    exp = Experiment()
    status = exp.status()
    assert status["running"] is False
    assert status["braid_folder"] is None
    assert status["end_time"] is None
    assert status["processes"] == {}


def test_prepare_braid_folder_returns_the_folder(config_path, patch_processes):
    exp = Experiment()
    folder = exp.prepare_braid_folder(config_path)
    assert folder == str(patch_processes)


class _FakeAliveProcess:
    def is_alive(self):
        return True


class _FakeDeadProcess:
    def is_alive(self):
        return False


def test_dead_braid_publisher_produces_its_own_message_not_hardware():
    """A BraidPublisher init failure must be diagnosed as a Braid
    connectivity issue, not misattributed to lens/opto hardware."""
    processes = [
        ("BraidPublisher", _FakeDeadProcess()),
        ("TriggerHandler", _FakeAliveProcess()),
    ]

    messages = orchestration._check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "BraidPublisher" in messages[0]
    assert "Braid" in messages[0]
    assert "hardware" not in messages[0].lower()


def test_dead_liquid_lens_produces_hardware_message():
    processes = [("LiquidLens", _FakeDeadProcess())]

    messages = orchestration._check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "LiquidLens" in messages[0]
    assert "hardware" in messages[0].lower()


def test_dead_opto_trigger_worker_produces_hardware_message():
    processes = [("OptoTriggerWorker", _FakeDeadProcess())]

    messages = orchestration._check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "OptoTriggerWorker" in messages[0]
    assert "hardware" in messages[0].lower()


def test_all_alive_produces_no_messages():
    processes = [
        ("BraidPublisher", _FakeAliveProcess()),
        ("LiquidLens", _FakeAliveProcess()),
        ("OptoTriggerWorker", _FakeAliveProcess()),
    ]

    assert orchestration._check_critical_processes_alive(processes) == []


def test_non_critical_process_death_is_ignored():
    """A dead Monitoring Server/VisualProcess/CameraProcess must not abort
    the whole experiment -- only processes known to fail fast and
    unrecoverably during their own init are critical."""
    processes = [
        ("Monitoring Server", _FakeDeadProcess()),
        ("VisualProcess", _FakeDeadProcess()),
        ("CameraProcess", _FakeDeadProcess()),
    ]

    assert orchestration._check_critical_processes_alive(processes) == []


def test_dead_trigger_handler_produces_its_own_message():
    """TriggerHandler binds its own ZMQ publisher socket during init and
    exits unrecoverably if the port is already taken -- the same fail-fast
    profile as the other critical processes, so its death during init must
    also be treated as fatal rather than silently running a 24-hour
    experiment with no tracking, no triggers, no recordings."""
    processes = [("TriggerHandler", _FakeDeadProcess())]

    messages = orchestration._check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "TriggerHandler" in messages[0]


def test_multiple_dead_critical_processes_each_produce_a_message():
    processes = [
        ("BraidPublisher", _FakeDeadProcess()),
        ("LiquidLens", _FakeDeadProcess()),
    ]

    messages = orchestration._check_critical_processes_alive(processes)

    assert len(messages) == 2
    joined = " ".join(messages)
    assert "BraidPublisher" in joined
    assert "LiquidLens" in joined
