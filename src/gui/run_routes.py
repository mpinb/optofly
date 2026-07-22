"""Run tab: metadata form, Braid-folder check, start/stop."""

from flask import Blueprint, current_app, jsonify, render_template, request

from src.orchestration import ExperimentAlreadyRunningError, ExperimentStartError
from src.utils.braid import BraidFolderError
from src.utils.metadata import metadata_from_form

run_bp = Blueprint("run", __name__)


@run_bp.route("/run")
def run_page():
    return render_template("run.html")


@run_bp.route("/run/prepare", methods=["POST"])
def run_prepare():
    experiment = current_app.config["EXPERIMENT"]
    config_path = request.form.get("config_path", "configs/config.toml")
    try:
        braid_folder = experiment.prepare_braid_folder(config_path)
    except BraidFolderError as e:
        return jsonify({"error": str(e)}), 422
    return jsonify({"braid_folder": braid_folder})


@run_bp.route("/run/start", methods=["POST"])
def run_start():
    experiment = current_app.config["EXPERIMENT"]
    config_path = request.form.get("config_path", "configs/config.toml")
    metadata = metadata_from_form(request.form)

    try:
        experiment.start(config_path, metadata)
    except ExperimentAlreadyRunningError as e:
        return jsonify({"error": str(e)}), 409
    except ExperimentStartError as e:
        return jsonify({"error": str(e)}), 422
    except BraidFolderError as e:
        return jsonify({"error": str(e)}), 422

    return jsonify({"status": "started"})


@run_bp.route("/run/stop", methods=["POST"])
def run_stop():
    experiment = current_app.config["EXPERIMENT"]
    experiment.stop()
    return jsonify({"status": "stopped"})


def register(app):
    app.register_blueprint(run_bp)
