"""Flask app factory for the OptoFly GUI.

Run with `uv run python -m src.gui`. Single Python process, single Flask
app: one Experiment instance lives for the process's lifetime at
app.config["EXPERIMENT"], shared by every blueprint registered here.
"""

from flask import Flask, jsonify, redirect

from src.orchestration import Experiment
from src.gui import run_routes, config_routes


def create_app(experiment: Experiment | None = None) -> Flask:
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

    return app
