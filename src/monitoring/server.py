from flask import Flask, render_template, jsonify, Response
import collections
import logging
import zmq
import threading
import json
import queue
import uuid
import datetime

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global dict of queues
client_queues = {}
queues_lock = threading.Lock()

# How many recent triggers to keep for the dashboard. /api/triggers returns the
# whole list on every poll, so this is bounded rather than growing for the life
# of a 24-hour run; "count" below stays a true running total.
TRIGGER_HISTORY = 500

# In-memory storage for trigger data
trigger_data = {
    "count": 0,
    "triggers": collections.deque(maxlen=TRIGGER_HISTORY),
}

# Thread-safe queue and lock for trigger data
trigger_lock = threading.Lock()
trigger_queue = queue.Queue()


def zmq_listener(zmq_address="tcp://localhost:23456", zone_enter_topic="ZONE_ENTER"):
    """Listen for ZONE_ENTER messages over ZMQ and update trigger_data.

    Decoding and dispatch are guarded, but receive failures are not, and the
    split is deliberate. A malformed message used to end this daemon thread
    while Flask carried on serving, so the dashboard stayed up and silently
    stopped updating -- and an operator reading a stale-but-live dashboard
    concludes no flies are triggering. A dead socket, by contrast, is not
    recoverable here; the thread ends, but loudly.
    """
    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)
    subscriber.connect(zmq_address)
    subscriber.setsockopt_string(zmq.SUBSCRIBE, zone_enter_topic)

    while True:
        parts = subscriber.recv_multipart()

        try:
            topic_bytes, message = parts
            topic = topic_bytes.decode("utf-8")
            if topic != zone_enter_topic:
                continue
            data = json.loads(message.decode("utf-8"))
        except Exception:
            logger.exception("Monitoring listener: dropping unreadable message")
            continue

        with trigger_lock:
            trigger_data["count"] += 1
            trigger_data["triggers"].append(data)

        with queues_lock:
            for _, q in list(client_queues.items()):
                q.put(data)


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/triggers")
def get_triggers():
    with trigger_lock:
        # triggers is a bounded deque, which jsonify cannot serialize; copy to
        # a list inside the lock so the response is also a stable snapshot.
        return jsonify(
            {"count": trigger_data["count"], "triggers": list(trigger_data["triggers"])}
        )


@app.route("/stream")
def stream():
    def event_stream():
        client_id = str(uuid.uuid4())

        my_queue = queue.Queue()

        with queues_lock:
            client_queues[client_id] = my_queue

        try:
            while True:
                trigger = my_queue.get()

                # Add current time HHMMSS to the trigger data as the first key
                trigger["time"] = datetime.datetime.now().strftime("%H%M%S")

                data = json.dumps({"count": trigger_data["count"], "trigger": trigger})

                yield f"data: {data}\n\n"
        finally:
            with queues_lock:
                del client_queues[client_id]

    return Response(event_stream(), mimetype="text/event-stream")


def run_server(
    zmq_address="tcp://localhost:23456",
    host="0.0.0.0",
    port=5000,
    zone_enter_topic="ZONE_ENTER",
):
    zmq_thread = threading.Thread(
        target=zmq_listener, args=(zmq_address, zone_enter_topic), daemon=True
    )
    zmq_thread.start()

    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    run_server()
