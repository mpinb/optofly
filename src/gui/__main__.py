"""Entry point: `uv run python -m src.gui`."""

from src.gui.app import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
