"""
OptoFly Main Experiment Launcher

Config-driven experiment launcher that starts processes based on configs/config.toml settings.
Automatically enables/disables processes based on their 'active' flags.
"""

import argparse
import multiprocessing as mp
import sys
import time
import tomllib
from datetime import datetime

from src.orchestration import Experiment, ExperimentStartError
from src.utils.braid import BraidFolderError
from src.utils.config import AppConfig
from src.utils.metadata import UserCancelledError, collect_metadata


def load_config(config_path: str) -> AppConfig:
    """Load and validate configuration from TOML file.

    Args:
        config_path: Path to the configuration file

    Returns:
        The fully assembled, typed configuration tree.
    """
    try:
        return AppConfig.load(config_path)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {config_path}")
        print("  Create it with: cp configs/config.example.toml configs/config.toml")
        sys.exit(1)
    except tomllib.TOMLDecodeError as e:
        # Distinguished from a ValueError below so the line/column tomllib
        # reports isn't flattened into a generic "failed to load" message.
        print(f"ERROR: {config_path} is not valid TOML.")
        print(f"  {e}")
        sys.exit(1)
    except ValueError as e:
        # The message already opens with the config path (AppConfig.load adds
        # it), so don't repeat it in the header.
        print("ERROR: invalid configuration")
        for line in str(e).splitlines():
            print(f"  {line}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to load config {config_path}: {e}")
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


def print_experiment_config(app_config: AppConfig, active_processes: list):
    """Print experiment configuration summary."""
    print("\n" + "=" * 70)
    print("OptoFly Experiment Configuration")
    print("=" * 70)

    print("\nActive Processes:")
    for process_name in active_processes:
        print(f"  ✓ {process_name}")

    if "Monitoring Server" in active_processes:
        display_host = (
            "localhost" if app_config.monitoring.host == "0.0.0.0" else app_config.monitoring.host
        )
        print("\nMonitoring Dashboard:")
        print(f"  URL: http://{display_host}:{app_config.monitoring.port}")

    if "VisualProcess" in active_processes:
        enabled_stimuli = []
        try:
            with open(app_config.visual_stimuli.config_file, "rb") as f:
                vs_data = tomllib.load(f).get("visual_stimuli", {})
            for section_name, section in vs_data.items():
                if isinstance(section, dict) and section.get("enabled", False):
                    enabled_stimuli.append(section_name.replace("_", " ").capitalize())
        except Exception:
            pass  # config file not present; skip stimulus listing

        if enabled_stimuli:
            print("\nVisual Stimuli (Panda3D):")
            for stimulus in enabled_stimuli:
                print(f"  ✓ {stimulus}")

    if "OptoTriggerWorker" in active_processes:
        print("\nOpto Trigger:")
        print(f"  Color: {app_config.opto_trigger.color}")
        print(f"  Intensity: {app_config.opto_trigger.intensity}")
        print(f"  Duration: {app_config.opto_trigger.duration} ms")

    if "CameraProcess" in active_processes:
        print("\nCamera:")
        print(f"  Resolution: {app_config.camera.resolution}")
        print(f"  FPS: {app_config.camera.fps}")

    if "LiquidLens" in active_processes:
        print("\nLiquid Lens:")
        print(f"  Mode: {app_config.liquid_lens.mode}")
        print(f"  Predictor: {app_config.liquid_lens.predictor}")

    print("\nPress Ctrl+C to stop the experiment")
    print("=" * 70 + "\n")


def handle_metadata_cancellation(braid_proxy) -> None:
    """Stop Braid recording (if this run started it) and print a clean
    cancellation message.

    Called when collect_metadata() raises UserCancelledError. This must run
    before sys.exit(0) -- collect_metadata() is called before Experiment.start()
    (which is what would otherwise own stopping the recording), so nothing
    else in main() will stop a recording that prepare_braid_folder() already
    started.
    """
    print("\nMetadata collection cancelled by user.")
    if braid_proxy is not None:
        print("Stopping Braid recording...")
        try:
            braid_proxy.stop_csv_recording()
            print("✓ Recording stopped")
        except Exception as e:
            print(f"WARNING: Failed to stop recording: {e}")
    print("Exiting.")


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

    print(f"Loading configuration from {config_path}...")
    app_config = load_config(config_path)

    recording_time_warning = check_recording_time_sufficient(app_config)
    if recording_time_warning:
        print(f"WARNING: {recording_time_warning}")

    experiment = Experiment()

    try:
        braid_folder = experiment.prepare_braid_folder(config_path)
    except BraidFolderError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"Experiment data will be saved to: {braid_folder}")

    metadata = None
    if not args.skip_metadata:
        try:
            metadata = collect_metadata()
        except UserCancelledError:
            handle_metadata_cancellation(experiment.braid_proxy)
            sys.exit(0)
        experiment_duration = float(metadata.get("experiment_duration", 24))
    else:
        experiment_duration = 24.0
        print("⚠ Skipping metadata collection (--skip-metadata flag set)")

    print(f"Experiment duration set to {experiment_duration} hours.")

    start_failed = False
    try:
        experiment.start(config_path, metadata)

        status = experiment.status()
        end_time = status["end_time"]
        print(
            f"Experiment will end at: {datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        if status["log_path"]:
            print(f"Logging to: {status['log_path']}")

        print_experiment_config(app_config, list(status["processes"].keys()))

        while experiment.is_running():
            if datetime.now().timestamp() >= end_time:
                print("\n\nExperiment duration reached, shutting down...")
                break
            experiment.check_health()
            if not experiment.is_running():
                print("\n\nA critical process died during the run, shutting down...")
                for line in format_critical_failures(experiment.status()):
                    print(f"  {line}")
                break
            time.sleep(0.1)
    except ExperimentStartError as e:
        print(f"\nFATAL: {e}")
        start_failed = True
    except KeyboardInterrupt:
        print("\n\nReceived keyboard interrupt, shutting down...")
    except Exception as e:
        print(f"\n\nERROR during experiment: {e}")
        import traceback

        traceback.print_exc()
        raise
    finally:
        print("\nShutting down processes...")
        braid_folder_at_stop = experiment.status()["braid_folder"]
        experiment.stop()

        if braid_folder_at_stop:
            print("\n" + "=" * 70)
            print(f"Experiment ended. Data saved to: {braid_folder_at_stop}")
            print("=" * 70)
        else:
            print("\nExperiment terminated.")

    if start_failed:
        sys.exit(1)


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
