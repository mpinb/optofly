from main import check_critical_processes_alive, check_recording_time_sufficient


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

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "BraidPublisher" in messages[0]
    assert "Braid" in messages[0]
    assert "hardware" not in messages[0].lower()


def test_dead_liquid_lens_produces_hardware_message():
    processes = [("LiquidLens", _FakeDeadProcess())]

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "LiquidLens" in messages[0]
    assert "hardware" in messages[0].lower()


def test_dead_opto_trigger_worker_produces_hardware_message():
    processes = [("OptoTriggerWorker", _FakeDeadProcess())]

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 1
    assert "OptoTriggerWorker" in messages[0]
    assert "hardware" in messages[0].lower()


def test_all_alive_produces_no_messages():
    processes = [
        ("BraidPublisher", _FakeAliveProcess()),
        ("LiquidLens", _FakeAliveProcess()),
        ("OptoTriggerWorker", _FakeAliveProcess()),
    ]

    assert check_critical_processes_alive(processes) == []


def test_non_critical_process_death_is_ignored():
    """A dead Monitoring Server/VisualProcess/CameraProcess/TriggerHandler
    must not abort the whole experiment — only the three processes known
    to fail fast and unrecoverably during their own init are critical."""
    processes = [
        ("Monitoring Server", _FakeDeadProcess()),
        ("VisualProcess", _FakeDeadProcess()),
        ("CameraProcess", _FakeDeadProcess()),
        ("TriggerHandler", _FakeDeadProcess()),
    ]

    assert check_critical_processes_alive(processes) == []


def test_multiple_dead_critical_processes_each_produce_a_message():
    processes = [
        ("BraidPublisher", _FakeDeadProcess()),
        ("LiquidLens", _FakeDeadProcess()),
    ]

    messages = check_critical_processes_alive(processes)

    assert len(messages) == 2
    joined = " ".join(messages)
    assert "BraidPublisher" in joined
    assert "LiquidLens" in joined


def test_warns_when_max_recording_time_less_than_zone_timeout():
    config = {
        "camera": {"active": True, "max_recording_time": 1.0},
        "trigger_handler": {"zone_timeout": 3.0},
    }
    warning = check_recording_time_sufficient(config)
    assert warning is not None
    assert "1.0" in warning
    assert "3.0" in warning


def test_no_warning_when_camera_inactive():
    config = {
        "camera": {"active": False, "max_recording_time": 1.0},
        "trigger_handler": {"zone_timeout": 3.0},
    }
    assert check_recording_time_sufficient(config) is None


def test_no_warning_when_recording_time_sufficient():
    config = {
        "camera": {"active": True, "max_recording_time": 5.0},
        "trigger_handler": {"zone_timeout": 3.0},
    }
    assert check_recording_time_sufficient(config) is None
