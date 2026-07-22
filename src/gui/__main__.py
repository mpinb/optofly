"""Entry point: `uv run python -m src.gui`."""

import multiprocessing as mp

from src.gui.app import create_app

if __name__ == "__main__":
    # Required for pyglet/OpenGL contexts to work in child processes (see
    # main.py). Doubly important here: by the time a GUI request triggers
    # Experiment.start(), this process is already multi-threaded (Flask's
    # threaded server + the Monitor tab's background thread), and forking a
    # multi-threaded process risks deadlocks.
    mp.set_start_method("spawn", force=True)
    app = create_app()
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
