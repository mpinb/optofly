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


def test_index_is_pruned_when_events_evicted():
    """_index shouldn't grow unbounded — once a trigger's event has been
    evicted from the capped _events deque, enrich() on its key should find
    nothing rather than resurrecting a stale entry forever."""
    state = MonitorState(max_events=3)
    for i in range(5):
        state.add_trigger({"obj_id": i, "frame": i, "timestamp": float(i)})

    # obj_id/frame 0 and 1 were evicted (only the newest 3 — 2,3,4 — remain).
    assert state.enrich("opto", {"obj_id": 0, "frame": 0, "value": "late"}) is None
    assert state.enrich("opto", {"obj_id": 1, "frame": 1, "value": "late"}) is None

    # obj_id/frame 4 is still present and should still enrich normally.
    enriched = state.enrich("opto", {"obj_id": 4, "frame": 4, "value": "on-time"})
    assert enriched is not None
    assert enriched["opto"]["value"] == "on-time"


def test_subscribe_receives_pushed_events():
    state = MonitorState()
    client_id, q = state.subscribe()

    state.add_trigger({"obj_id": 1, "frame": 100, "timestamp": 1.0})

    pushed = q.get(timeout=1)
    assert pushed["obj_id"] == 1

    state.unsubscribe(client_id)
