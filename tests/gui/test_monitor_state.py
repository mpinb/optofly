from src.gui.monitor_state import MonitorState


def test_add_trigger_increments_count_and_prepends():
    state = MonitorState()
    state.add_trigger({"obj_id": 1, "frame": 100, "timestamp": 1.0})
    state.add_trigger({"obj_id": 2, "frame": 200, "timestamp": 2.0})

    snapshot = state.snapshot()
    assert snapshot["count"] == 2
    assert snapshot["events"][0]["obj_id"] == 2  # newest first
    assert snapshot["events"][1]["obj_id"] == 1


def test_events_list_is_capped(monkeypatch):
    state = MonitorState(max_events=3)
    for i in range(5):
        state.add_trigger({"obj_id": i, "frame": i, "timestamp": float(i)})

    snapshot = state.snapshot()
    assert len(snapshot["events"]) == 3
    assert snapshot["events"][0]["obj_id"] == 4  # newest kept


def test_subscribe_receives_pushed_events():
    state = MonitorState()
    client_id, q = state.subscribe()

    state.add_trigger({"obj_id": 1, "frame": 100, "timestamp": 1.0})

    pushed = q.get(timeout=1)
    assert pushed["obj_id"] == 1

    state.unsubscribe(client_id)
