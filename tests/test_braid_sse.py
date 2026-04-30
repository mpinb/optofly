import json

from src.processes.braid import iter_sse_events


def braid_frame(payload):
    return f"event: braid\ndata: {json.dumps(payload)}\n\n"


def test_iter_sse_events_handles_split_event_lines():
    payload = {"msg": {"Update": {"obj_id": 7, "frame": 12}}}
    lines = ["event: braid", f"data: {json.dumps(payload)}", ""]

    assert list(iter_sse_events(lines)) == [("braid", json.dumps(payload))]


def test_iter_sse_events_handles_coalesced_events():
    first = {"msg": {"Update": {"obj_id": 7, "frame": 12}}}
    second = {"msg": {"Death": 7}}
    lines = (braid_frame(first) + braid_frame(second)).splitlines()

    assert list(iter_sse_events(lines)) == [
        ("braid", json.dumps(first)),
        ("braid", json.dumps(second)),
    ]


def test_iter_sse_events_ignores_comments_and_unknown_fields():
    payload = {"msg": {"Update": {"obj_id": 7, "frame": 12}}}
    lines = [
        ": keepalive",
        "id: 99",
        "retry: 1000",
        "event: braid",
        f"data: {json.dumps(payload)}",
        "",
    ]

    assert list(iter_sse_events(lines)) == [("braid", json.dumps(payload))]


def test_iter_sse_events_joins_multiline_data_fields():
    lines = [
        "event: braid",
        'data: {"msg":',
        'data: {"Death": 7}}',
        "",
    ]

    assert list(iter_sse_events(lines)) == [("braid", '{"msg":\n{"Death": 7}}')]


def test_iter_sse_events_flushes_final_event_without_blank_line():
    payload = {"msg": {"Death": 7}}
    lines = ["event: braid", f"data: {json.dumps(payload)}"]

    assert list(iter_sse_events(lines)) == [("braid", json.dumps(payload))]
