"""
OptoFly Main Experiment Launcher

Config-driven experiment launcher that starts processes based on configs/config.toml settings.
Automatically enables/disables processes based on their 'active' flags.
"""

import argparse
import logging
import multiprocessing as mp
import sys
import time
from datetime import datetime

from src.orchestration import Experiment, ExperimentStartError
from src.utils.braid import BraidFolderError
from src.utils.config import AppConfig
from src.utils.metadata import UserCancelledError, collect_metadata

logger = logging.getLogger(__name__)


def load_config(config_path: str) -> AppConfig:
    """Load and validate configuration from TOML file.

    Args:
        config_path: Path to the configuration file

    Returns:
        The fully assembled, typed configuration tree.
    """
    import tomllib

    try:
        return AppConfig.load(config_path)
    except FileNotFoundError:
        logger.error("Config file not found: %s", config_path)
        logger.error("  Create it with: cp configs/config.example.toml configs/config.toml")
        sys.exit(1)
    except tomllib.TOMLDecodeError as e:
        logger.error("%s is not valid TOML.", config_path)
        logger.error("  %s", e)
        sys.exit(1)
    except ValueError as e:
        logger.error("invalid configuration")
        for line in str(e).splitlines():
            logger.error("  %s", line)
        sys.exit(1)
    except Exception as e:
        logger.error("Failed to load config %s: %s", config_path, e)
        sys.exit(1)


def check_recording_time_sufficient(app_config: AppConfig) -> str | None:
    """Return a warning message if camera.max_recording_time is shorter
    than trigger_handler.zone_timeout, or None if camera is inactive or
    the durations are sufficient.
    """
    if not app_config.camera.active:
        return None
    if app_config.camera.max_recording_time < app_config.trigger_handler.zone_timeout:
        return (
            f"camera.max_recording_time ({app_config.camera.max_recording_time}s) is "
            f"less than trigger_handler.zone_timeout "
            f"({app_config.trigger_handler.zone_timeout}s). Every recording will be "
            "truncated before the fly exits the zone."
        )
    return None


def format_critical_failures(status: dict) -> list[str]:
    """Return one line per process that recorded a failure reason.

    Experiment already stores why each process died; without this the operator
    returning to a finished 24-hour run saw only "A critical process died",
    with the actual cause somewhere up the scrollback (or gone).
    """
    return [
        f"✗ {info['failed_reason']}"
        for info in status["processes"].values()
        if info.get("failed_reason")
    ]


def handle_metadata_cancellation(braid_proxy) -> None:
    """Stop Braid recording (if this run started it) and print a clean
    cancellation message.

    Called when collect_metadata() raises UserCancelledError. This must run
    before sys.exit(0) -- collect_metadata() is called before Experiment.start()
    (which is what would otherwise own stopping the recording), so nothing
    else in main() will stop a recording that prepare_braid_folder() already
    started.
    """
    logger.info("Metadata collection cancelled by user.")
    if braid_proxy is not None:
        logger.info("Stopping Braid recording...")
        try:
            braid_proxy.stop_csv_recording()
            logger.info("Recording stopped")
        except Exception as e:
            logger.warning("Failed to stop recording: %s", e)
    logger.info("Exiting.")


def main():
    """Launch OptoFly experiment with config-driven process selection."""

    parser = argparse.ArgumentParser(description="OptoFly experiment launcher")
    parser.add_argument(
        "--skip-metadata",
        action="store_true",
        help="Skip experiment metadata prompt (for quick tests)",
    )
    parser.add_argument(
        "--config",
        default="configs/config.toml",
        help="Path to TOML configuration file (default: configs/config.toml)",
    )
    args = parser.parse_args()
    config_path = args.config

    logger.info("Loading configuration from %s...", config_path)
    app_config = load_config(config_path)

    recording_time_warning = check_recording_time_sufficient(app_config)
    if recording_time_warning:
        logger.warning(recording_time_warning)

    experiment = Experiment()

    try:
        braid_folder = experiment.prepare_braid_folder(config_path)
    except BraidFolderError as e:
        logger.error("%s", e)
        sys.exit(1)
    logger.info("Experiment data will be saved to: %s", braid_folder)

    metadata = None
    if not args.skip_metadata:
        try:
            metadata = collect_metadata()
        except UserCancelledError:
            handle_metadata_cancellation(experiment.braid_proxy)
            sys.exit(0)
    else:
        logger.info("Skipping metadata (--skip-metadata)")

    start_failed = False
    try:
        experiment.start(config_path, metadata)

        logger.info("All systems ready. Ctrl+C to stop.")
        end_time = experiment.status()["end_time"]

        while experiment.is_running():
            if datetime.now().timestamp() >= end_time:
                print("\n── Experiment duration reached ──")
                break
            experiment.check_health()
            if not experiment.is_running():
                print("\n── Critical process died ──")
                for line in format_critical_failures(experiment.status()):
                    print(f"  {line}")
                break
            time.sleep(0.1)
    except ExperimentStartError as e:
        logger.critical("FATAL: %s", e)
        start_failed = True
    except KeyboardInterrupt:
        print("\n── Shutting down (Ctrl+C) ──")
    except Exception as e:
        logger.error("ERROR during experiment: %s", e)
        import traceback

        traceback.print_exc()
        raise
    finally:
        logger.info("Shutting down processes...")
        braid_folder_at_stop = experiment.status()["braid_folder"]
        experiment.stop()

        if braid_folder_at_stop:
            print(f"\n── Experiment ended. Data: {braid_folder_at_stop} ──")
        else:
            print("── Experiment terminated ──")

    if start_failed:
        sys.exit(1)


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
