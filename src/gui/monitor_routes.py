"""Monitor tab: process health (via /api/status) + live trigger feed (SSE)."""

import json

from flask import Blueprint, Response, current_app, render_template

monitor_bp = Blueprint("monitor", __name__)


@monitor_bp.route("/monitor")
def monitor_page():
    return render_template("monitor.html")


@monitor_bp.route("/monitor/stream")
def monitor_stream():
    state = current_app.config["MONITOR_STATE"]

    def event_stream():
        client_id, q = state.subscribe()
        try:
            while True:
                event = q.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            state.unsubscribe(client_id)

    return Response(event_stream(), mimetype="text/event-stream")


def register(app):
    app.register_blueprint(monitor_bp)
