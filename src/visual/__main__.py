"""Run the Panda3D visual stimuli process directly.

Usage:
    uv run python -m src.visual --standalone   # small 1280x320 test window, no ZMQ
    uv run python -m src.visual                # full window, subscribes to ZONE_ENTER
"""

import argparse
import multiprocessing as mp

from src.visual.process import VisualProcess


def main() -> None:
    parser = argparse.ArgumentParser(description="Visual stimuli (Panda3D)")
    parser.add_argument("--config", default="configs/config.toml")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Run without ZMQ in a small test window",
    )
    args = parser.parse_args()

    event = mp.Event()
    proc = VisualProcess(
        config_path=args.config,
        event=event,
        standalone=args.standalone,
    )
    proc._run()


if __name__ == "__main__":
    main()
