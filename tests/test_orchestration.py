from pathlib import Path

import pytest

import src.orchestration as orchestration
from src.orchestration import Experiment
from src.utils.config import AppConfig


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
    assert messages[0] == (
        "BraidPublisher process exited during initialization. "
        "Check that Braid is running and reachable at the configured "
        "host/port in config.toml."
    )


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


def test_inactive_opto_trigger_is_not_critical(config_path):
    """configs/config.example.toml ships opto_trigger.active = false. A user
    with no Arduino wired up must still be able to run the experiment, so a
    dead OptoTriggerWorker is only fatal when stimulation was requested."""
    app_config = AppConfig.load(config_path)
    assert app_config.opto_trigger.active is False

    critical = orchestration._critical_names(app_config)

    assert "OptoTriggerWorker" not in critical
    assert {"BraidPublisher", "TriggerHandler"} <= critical


def test_active_opto_trigger_is_critical(tmp_path, config_path):
    source = Path(config_path).read_text()
    active_config = tmp_path / "opto_active.toml"
    active_config.write_text(
        source.replace("[opto_trigger]\n# LED optogenetic stimulation\nactive = false",
                       "[opto_trigger]\n# LED optogenetic stimulation\nactive = true")
    )

    app_config = AppConfig.load(str(active_config))
    assert app_config.opto_trigger.active is True

    assert "OptoTriggerWorker" in orchestration._critical_names(app_config)


def test_dead_inactive_opto_trigger_produces_no_fatal_message():
    """End-to-end of the above through the message builder."""
    processes = [("OptoTriggerWorker", _FakeDeadProcess())]
    critical = {"BraidPublisher", "TriggerHandler"}

    assert orchestration._check_critical_processes_alive(processes, critical) == []


def test_liquid_lens_is_not_critical_when_camera_is_inactive(tmp_path, config_path):
    """LiquidLens is only started when camera.active is true, so it must not
    be judged critical when the camera is off."""
    source = Path(config_path).read_text()
    no_camera = tmp_path / "no_camera.toml"
    no_camera.write_text(
        source.replace("# High-speed camera settings\nactive = true",
                       "# High-speed camera settings\nactive = false")
    )

    app_config = AppConfig.load(str(no_camera))
    assert app_config.camera.active is False

    assert "LiquidLens" not in orchestration._critical_names(app_config)


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


class FakeCrashingProcess(FakeProcess):
    """Simulates LiquidLens/OptoTriggerWorker exiting immediately on hardware failure."""

    def start(self):
        self._alive = False  # dies before the init-check sleep in Experiment.start()


def test_start_spawns_core_processes_and_status_reports_running(config_path):
    exp = Experiment()
    exp.start(config_path, metadata=None)

    status = exp.status()
    assert status["running"] is True
    assert "BraidPublisher" in status["processes"]
    assert "TriggerHandler" in status["processes"]
    assert "LatencyLogger" in status["processes"]
    assert "OptoTriggerWorker" in status["processes"]
    assert status["processes"]["BraidPublisher"]["alive"] is True

    exp.stop()


def test_start_spawns_active_flag_gated_processes(config_path):
    """configs/config.example.toml has monitoring.active, camera.active, and
    visual_stimuli.active all set to true -- exercise the gated branches and
    confirm they actually spawn, so a regression in the `active`-flag
    checks (wrong attribute, inverted condition, code moved outside its
    `if`) fails a test instead of silently skipping these processes."""
    exp = Experiment()
    exp.start(config_path, metadata=None)

    processes = exp.status()["processes"]
    assert "Monitoring Server" in processes
    assert "VisualProcess" in processes
    assert "CameraProcess" in processes
    assert "LiquidLens" in processes

    exp.stop()


def test_start_while_running_raises(config_path):
    from src.orchestration import ExperimentAlreadyRunningError

    exp = Experiment()
    exp.start(config_path, metadata=None)
    with pytest.raises(ExperimentAlreadyRunningError):
        exp.start(config_path, metadata=None)
    exp.stop()


def test_critical_process_failure_raises_start_error(monkeypatch, config_path):
    from src.orchestration import ExperimentStartError

    # LiquidLens, not OptoTriggerWorker: the example config sets
    # opto_trigger.active = false, which (deliberately) makes a dead
    # OptoTriggerWorker non-fatal. camera.active is true there, so LiquidLens
    # is critical and exercises the same fail-fast path.
    monkeypatch.setattr(orchestration, "LiquidLens", FakeCrashingProcess)
    exp = Experiment()
    with pytest.raises(ExperimentStartError):
        exp.start(config_path, metadata=None)

    status = exp.status()
    assert status["processes"]["LiquidLens"]["failed_reason"] is not None
    exp.stop()


def test_prepare_braid_folder_is_reused_by_start(config_path, patch_processes):
    exp = Experiment()
    folder = exp.prepare_braid_folder(config_path)
    assert folder == str(patch_processes)

    exp.start(config_path, metadata=None)
    assert exp.status()["braid_folder"] == folder
    exp.stop()


class FakeHungProcess(FakeProcess):
    """Never dies on join() -- forces Experiment.stop() to terminate() it."""

    def join(self, timeout=None):
        pass  # stays alive


def test_stop_reports_clean_shutdown(config_path):
    exp = Experiment()
    exp.start(config_path, metadata=None)
    exp.stop()

    status = exp.status()
    assert status["running"] is False
    for info in status["processes"].values():
        assert info["shutdown"] == "clean"


def test_stop_force_terminates_hung_process(monkeypatch, config_path):
    monkeypatch.setattr(orchestration, "OptoTriggerWorker", FakeHungProcess)
    exp = Experiment()
    exp.start(config_path, metadata=None)
    exp.stop()

    status = exp.status()
    assert status["processes"]["OptoTriggerWorker"]["shutdown"] == "forced"
    hung = [p for p in FakeProcess.instances if isinstance(p, FakeHungProcess)][0]
    assert hung.terminated is True


def test_needs_cleanup_true_when_stop_event_set_externally(config_path):
    exp = Experiment()
    exp.start(config_path, metadata=None)

    # Simulate a mid-run crash / anything setting the shared stop event
    # directly, without going through Experiment.stop().
    exp._stop_event.set()

    assert exp.needs_cleanup() is True
    assert exp.is_running() is False  # is_running() flips immediately...

    exp.stop()
    assert exp.needs_cleanup() is False  # ...but needs_cleanup() clears once stop() actually runs


def test_start_writes_metadata_when_provided(monkeypatch, config_path):
    write_metadata_calls = []
    extract_config_columns_calls = []
    append_metadata_to_csv_calls = []
    monkeypatch.setattr(
        orchestration,
        "write_metadata",
        lambda metadata, braid_folder: write_metadata_calls.append((metadata, braid_folder)),
    )
    monkeypatch.setattr(
        orchestration,
        "extract_config_columns",
        lambda config_path: extract_config_columns_calls.append(config_path) or ["researcher"],
    )
    monkeypatch.setattr(
        orchestration,
        "append_metadata_to_csv",
        lambda metadata, braid_folder, config_columns: append_metadata_to_csv_calls.append(
            (metadata, braid_folder, config_columns)
        ),
    )

    exp = Experiment()
    metadata = {"experiment_duration": 2, "researcher": "test"}
    exp.start(config_path, metadata=metadata)

    braid_folder = exp.status()["braid_folder"]
    assert write_metadata_calls == [(metadata, braid_folder)]
    assert extract_config_columns_calls == [config_path]
    assert append_metadata_to_csv_calls == [(metadata, braid_folder, ["researcher"])]

    exp.stop()


def test_check_health_is_a_no_op_before_start():
    exp = Experiment()
    exp.check_health()  # must not raise


def test_check_health_sets_stop_event_when_critical_process_dies_mid_run(config_path):
    exp = Experiment()
    exp.start(config_path, metadata=None)
    assert exp.is_running() is True

    # Simulate LiquidLens dying mid-run (it started alive, per FakeProcess.start()).
    # LiquidLens rather than OptoTriggerWorker because the example config
    # disables opto stimulation, which makes that process non-critical.
    lens = [p for name, p in exp._processes if name == "LiquidLens"][0]
    lens._alive = False

    exp.check_health()

    assert exp.is_running() is False
    assert exp.status()["processes"]["LiquidLens"]["failed_reason"] is not None
    exp.stop()


def test_check_health_logs_once_for_non_critical_process_death(config_path, caplog, monkeypatch):
    import logging

    # start() calls the real configure_process_logging(), which -- by design,
    # to give each spawned process a clean handler set -- clears every handler
    # on the root logger, including pytest's caplog capture handler. Stub it
    # out here so caplog can still observe log records emitted after start().
    monkeypatch.setattr(orchestration, "configure_process_logging", lambda *a, **kw: None)

    exp = Experiment()
    exp.start(config_path, metadata=None)

    latency_logger = [p for name, p in exp._processes if name == "LatencyLogger"][0]
    latency_logger._alive = False

    # Discard start()'s own INFO logs (e.g. "  ✓ LatencyLogger") so the
    # substring match below only sees what check_health() itself logs.
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="src.orchestration"):
        exp.check_health()
        exp.check_health()  # second call must not log again

    matching = [r for r in caplog.records if "LatencyLogger" in r.message]
    assert len(matching) == 1
    assert exp.is_running() is True  # non-critical death is never fatal
    exp.stop()


class _RecordingBraidProxy:
    def __init__(self):
        self.stopped = False

    def stop_csv_recording(self):
        self.stopped = True


def test_stop_ends_the_braid_recording_when_start_failed_before_the_stop_event(
    monkeypatch, config_path, patch_processes
):
    """prepare_braid_folder() starts a Braid recording. If start() then raises
    before it assigns _stop_event -- writing metadata, copying configs,
    configuring logging, constructing the first process -- stop() used to
    return immediately at `if self._stop_event is None`, never reaching the
    braid_proxy teardown. Braid then records forever with nobody to stop it,
    and the next run starts a second one alongside it."""
    exp = Experiment()
    exp.prepare_braid_folder(config_path)
    proxy = _RecordingBraidProxy()
    exp._braid_proxy = proxy

    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(orchestration, "write_metadata", explode)

    with pytest.raises(OSError):
        exp.start(config_path, metadata={"experiment_duration": 1})

    exp.stop()

    assert proxy.stopped is True, "a half-started experiment must still stop recording"


def test_stop_ends_the_braid_recording_after_a_normal_run(config_path, patch_processes):
    exp = Experiment()
    exp.prepare_braid_folder(config_path)
    proxy = _RecordingBraidProxy()
    exp._braid_proxy = proxy

    exp.start(config_path, metadata=None)
    exp.stop()

    assert proxy.stopped is True


def test_stop_is_still_a_no_op_with_nothing_started():
    Experiment().stop()  # must not raise
