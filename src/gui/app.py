"""Flask app factory for the OptoFly GUI.

Run with `uv run python -m src.gui`. Single Python process, single Flask
app: one Experiment instance lives for the process's lifetime at
app.config["EXPERIMENT"], shared by every blueprint registered here.
"""

import threading

from flask import Flask, jsonify, redirect

from src.gui import advanced_routes, config_routes, monitor_routes, run_routes
from src.gui.monitor_state import MonitorState
from src.gui.monitor_worker import start_monitor_thread
from src.orchestration import Experiment


def create_app(
    experiment: Experiment | None = None,
    config_path: str | None = "configs/config.toml",
) -> Flask:
    """config_path picks which file's ZMQ ports the Monitor tab's background
    thread subscribes to. Pass None (as GUI route/blueprint tests do) to skip
    starting that thread entirely — those tests don't need a live ZMQ socket."""
    app = Flask(__name__)
    app.config["EXPERIMENT"] = experiment if experiment is not None else Experiment()

    @app.route("/")
    def index():
        return redirect("/run")

    @app.route("/api/status")
    def api_status():
        return jsonify(app.config["EXPERIMENT"].status())

    run_routes.register(app)
    config_routes.register(app)
    advanced_routes.register(app)

    monitor_state = MonitorState()
    app.config["MONITOR_STATE"] = monitor_state
    app.config["MONITOR_STOP_EVENT"] = threading.Event()
    if config_path:
        start_monitor_thread(
            config_path, app.config["EXPERIMENT"], monitor_state, app.config["MONITOR_STOP_EVENT"]
        )
    monitor_routes.register(app)

    return app
