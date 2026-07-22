"""Config tab: comment-preserving form editor for the commonly-tweaked
config.toml / visual_stimuli.toml fields (see src/gui/config_editor.py for
the exact field list — everything else stays on the Advanced tab)."""

from flask import Blueprint, jsonify, render_template, request

from src.gui import advanced_routes
from src.gui.config_editor import (
    CONFIG_TOML_FIELDS,
    VISUAL_STIMULI_TOML_FIELDS,
    coerce_form_value,
    load_fields,
    save_fields,
)

config_bp = Blueprint("config", __name__)

_FIELD_SETS = {
    "config": (CONFIG_TOML_FIELDS, "configs/config.toml"),
    "visual_stimuli": (VISUAL_STIMULI_TOML_FIELDS, "configs/visual_stimuli.toml"),
}


@config_bp.route("/config")
def config_page():
    kind = request.args.get("kind", "config")
    fields, default_path = _FIELD_SETS[kind]
    path = request.args.get("path", default_path)
    if path not in advanced_routes.ALLOWED_PATHS:
        return jsonify({"error": "Path not allowed"}), 404

    try:
        values = load_fields(path, fields)
    except FileNotFoundError:
        values = {".".join(f[0]): None for f in fields}

    display_fields = [
        (".".join(toml_path), values[".".join(toml_path)], field_type.__name__)
        for toml_path, field_type in fields
    ]

    return render_template("config.html", path=path, kind=kind, fields=display_fields)


@config_bp.route("/config/save", methods=["POST"])
def config_save():
    kind = request.form.get("kind", "config")
    fields, default_path = _FIELD_SETS[kind]
    path = request.form.get("path", default_path)
    if path not in advanced_routes.ALLOWED_PATHS:
        return jsonify({"error": "Path not allowed"}), 422

    try:
        updates = {}
        for toml_path, field_type in fields:
            dotted = ".".join(toml_path)
            raw = request.form.get(dotted)
            updates[dotted] = coerce_form_value(raw, field_type)

        save_fields(path, fields, updates)
    except (FileNotFoundError, ValueError, TypeError, KeyError) as e:
        return jsonify({"error": str(e)}), 422

    return jsonify({"status": "saved"})


def register(app):
    app.register_blueprint(config_bp)
