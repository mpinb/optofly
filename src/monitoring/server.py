from flask import Flask, render_template, jsonify, Response
import zmq
import threading
import json
import queue
import uuid
import datetime

app = Flask(__name__)

# Global dict of queues
client_queues = {}
queues_lock = threading.Lock()

# In-memory storage for trigger data
trigger_data = {
    "count": 0,
    "triggers": [],
}

# Thread-safe queue and lock for trigger data
trigger_lock = threading.Lock()
trigger_queue = queue.Queue()


def zmq_listener(zmq_address="tcp://localhost:23456"):
    """Listen for TRIGGER messages over ZMQ and update trigger_data."""
    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)
    subscriber.connect(zmq_address)
    subscriber.setsockopt_string(zmq.SUBSCRIBE, "TRIGGER")

    while True:
        message = subscriber.recv_string()
        topic, json_data = message.split(" ", 1)

        if topic == "TRIGGER":
            data = json.loads(json_data)

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
        return jsonify(trigger_data)


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


def run_server(zmq_address="tcp://localhost:23456", host="0.0.0.0", port=5000):
    zmq_thread = threading.Thread(target=zmq_listener, args=(zmq_address,), daemon=True)
    zmq_thread.start()

    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    run_server()
