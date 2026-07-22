"""Advanced tab: raw TOML editor for fields outside the Config tab's
common subset. tomllib validates before write; a parse error is returned
and the file on disk is left untouched."""

import os
import tomllib

from flask import Blueprint, jsonify, render_template, request

advanced_bp = Blueprint("advanced", __name__)

# Only allow editing these two config files through the Advanced tab
ALLOWED_PATHS = {"configs/config.toml", "configs/visual_stimuli.toml"}


@advanced_bp.route("/advanced")
def advanced_page():
    path = request.args.get("path", "configs/config.toml")
    if path not in ALLOWED_PATHS:
        return jsonify({"error": "Path not allowed"}), 404
    try:
        with open(path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    return render_template("advanced.html", path=path, content=content)


@advanced_bp.route("/advanced/save", methods=["POST"])
def advanced_save():
    path = request.form.get("path")
    content = request.form.get("content")

    if path is None or content is None:
        return jsonify({"error": "Missing required field: 'path' and 'content' are required"}), 422

    if path not in ALLOWED_PATHS:
        return jsonify({"error": "Path not allowed"}), 422

    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as e:
        return jsonify({"error": f"Invalid TOML: {e}"}), 422

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        f.write(content)
    os.replace(tmp_path, path)

    return jsonify({"status": "saved"})


def register(app):
    app.register_blueprint(advanced_bp)
