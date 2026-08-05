"""Entry point: `uv run python -m src.gui` or `uv run optofly-gui`."""

import multiprocessing as mp

from src.gui.app import create_app


def main():
    mp.set_start_method("spawn", force=True)
    app = create_app()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)


if __name__ == "__main__":
    main()
