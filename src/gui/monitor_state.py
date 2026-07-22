"""In-memory trigger feed state, shared between the background ZMQ/CSV
listener thread (src/gui/monitor_worker.py) and the SSE route
(src/gui/monitor_routes.py). Pure logic — no Flask, no ZMQ import here, so
it's testable without either."""

import collections
import queue
import threading
import uuid
from typing import Optional


class MonitorState:
    def __init__(self, max_events: int = 200):
        self._lock = threading.Lock()
        self._count = 0
        self._events = collections.deque(maxlen=max_events)
        self._index: dict[tuple[str, str], dict] = {}
        self._queues: dict[str, "queue.Queue"] = {}

    def add_trigger(self, data: dict) -> dict:
        with self._lock:
            self._count += 1
            event = {
                "obj_id": data.get("obj_id"),
                "frame": data.get("frame"),
                "timestamp": data.get("timestamp"),
                "opto": None,
                "stim": None,
            }
            if len(self._events) == self._events.maxlen:
                # appendleft() below will silently evict the oldest event
                # (the rightmost entry) — drop its _index entry too, or
                # _index grows unbounded for the life of the process.
                evicted = self._events[-1]
                evicted_key = (str(evicted["obj_id"]), str(evicted["frame"]))
                self._index.pop(evicted_key, None)
            self._events.appendleft(event)
            key = (str(event["obj_id"]), str(event["frame"]))
            self._index[key] = event
            self._broadcast(event)
            return event

    def enrich(self, kind: str, row: dict) -> Optional[dict]:
        """Attach a newly-tailed opto.csv/stim.csv row to its matching event."""
        with self._lock:
            key = (str(row.get("obj_id")), str(row.get("frame")))
            event = self._index.get(key)
            if event is not None:
                event[kind] = row
                self._broadcast(event)
            return event

    def snapshot(self) -> dict:
        with self._lock:
            return {"count": self._count, "events": list(self._events)}

    def subscribe(self) -> tuple[str, "queue.Queue"]:
        client_id = str(uuid.uuid4())
        q: "queue.Queue" = queue.Queue()
        with self._lock:
            self._queues[client_id] = q
        return client_id, q

    def unsubscribe(self, client_id: str) -> None:
        with self._lock:
            self._queues.pop(client_id, None)

    def _broadcast(self, event: dict) -> None:
        # Caller already holds self._lock.
        for q in self._queues.values():
            q.put(event)
