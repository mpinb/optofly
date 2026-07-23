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
import tomllib
from datetime import datetime
from pathlib import Path

from src.processes.braid import BraidPublisher
from src.processes.tracking import TriggerHandler
from src.visual.process import VisualProcess
from src.processes.led import OptoTriggerWorker
from src.processes.camera import RustCameraProcess as CameraProcess
from src.processes.lens import LiquidLens
from src.processes.latency_logger import LatencyLogger
from src.utils.braid import check_braid_folder_exists, verify_csv_files_in_braid
from src.utils.logger import configure_process_logging
from src.utils.metadata import (
    UserCancelledError,
    append_metadata_to_csv,
    collect_metadata,
    extract_config_columns,
    write_metadata,
)
from src.monitoring.server import run_server


def load_config(config_path: str) -> dict:
    """Load configuration from TOML file.

    Args:
        config_path: Path to the configuration file

    Returns:
        Dictionary containing the full configuration
    """
    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}")
        sys.exit(1)


def check_recording_time_sufficient(config: dict) -> str | None:
    """Return a warning message if camera.max_recording_time is shorter
    than trigger_handler.zone_timeout, or None if camera is inactive or
    the durations are sufficient.

    Every recording would otherwise be truncated before the fly exits the
    trigger zone. This check previously lived inside CameraConfig's
    constructor (which built a TriggerHandlerConfig just to read
    zone_timeout) -- that made CameraConfig and TriggerHandlerConfig
    mutually recursive. Doing it here, once, against the raw config dict
    main() already has, keeps the check without the cycle.
    """
    camera_cfg = config.get("camera", {})
    if not camera_cfg.get("active", False):
        return None
    max_recording_time = float(camera_cfg.get("max_recording_time", 3.0))
    zone_timeout = float(config.get("trigger_handler", {}).get("zone_timeout", 2.0))
    if max_recording_time < zone_timeout:
        return (
            f"camera.max_recording_time ({max_recording_time}s) is less than "
            f"trigger_handler.zone_timeout ({zone_timeout}s). Every recording "
            "will be truncated before the fly exits the zone."
        )
    return None


def print_experiment_config(config: dict, active_processes: list):
    """Print experiment configuration summary.

    Args:
        config: Loaded configuration dictionary
        active_processes: List of active process names
    """
    print("\n" + "=" * 70)
    print("OptoFly Experiment Configuration")
    print("=" * 70)

    print("\nActive Processes:")
    for process_name in active_processes:
        print(f"  ✓ {process_name}")

    # Monitoring server details
    if "Monitoring Server" in active_processes:
        monitoring_config = config.get("monitoring", {})
        host = monitoring_config.get("host", "0.0.0.0")
        port = monitoring_config.get("port", 5000)
        # If host is 0.0.0.0, show localhost for user convenience
        display_host = "localhost" if host == "0.0.0.0" else host
        print("\nMonitoring Dashboard:")
        print(f"  URL: http://{display_host}:{port}")

    # Visual stimuli details — read actual enabled flags from the stimuli config file
    if "VisualProcess" in active_processes:
        vs_cfg = config.get("visual_stimuli", {})
        vs_config_file = vs_cfg.get("config_file", "configs/visual_stimuli.toml")
        enabled_stimuli = []
        try:
            with open(vs_config_file, "rb") as f:
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

    # Opto trigger details
    if "OptoTriggerWorker" in active_processes:
        opto_config = config.get("opto_trigger", {})
        color = opto_config.get("color", "unknown")
        intensity = opto_config.get("intensity", "unknown")
        duration = opto_config.get("duration", "unknown")
        print("\nOpto Trigger:")
        print(f"  Color: {color}")
        print(f"  Intensity: {intensity}")
        print(f"  Duration: {duration} ms")

    # Camera details
    if "CameraProcess" in active_processes:
        camera_config = config.get("camera", {})
        fps = camera_config.get("fps", "unknown")
        resolution = camera_config.get("resolution", "unknown")
        print("\nCamera:")
        print(f"  Resolution: {resolution}")
        print(f"  FPS: {fps}")

    # Liquid lens details
    if "LiquidLens" in active_processes:
        lens_config = config.get("liquid_lens", {})
        mode = lens_config.get("mode", "diopter")
        predictor = lens_config.get("predictor", "none")
        print("\nLiquid Lens:")
        print(f"  Mode: {mode}")
        print(f"  Predictor: {predictor}")

    print("\nPress Ctrl+C to stop the experiment")
    print("=" * 70 + "\n")


# Processes that exit immediately and unrecoverably on their own init
# failure (bad serial port, unreachable Braid server, ZMQ bind conflict)
# rather than retrying in the background — safe to treat as fatal right
# after the startup grace period. Each gets its own diagnostic hint so a
# BraidPublisher connectivity failure is never misattributed to the lens
# or opto hardware.
_CRITICAL_INIT_HINTS = {
    "LiquidLens": "Check hardware connection and the relevant port in config.toml.",
    "OptoTriggerWorker": "Check hardware connection and the relevant port in config.toml.",
    "BraidPublisher": "Check that Braid is running and reachable at the configured host/port in config.toml.",
    "TriggerHandler": "Check that the ZMQ trigger_port in config.toml is not already in use by another process.",
}


def check_critical_processes_alive(processes: list) -> list[str]:
    """Return one FATAL message per critical process that died during init.

    `processes` is a list of (name, process) tuples, matching main()'s own
    `processes` list. Only names in _CRITICAL_INIT_HINTS are checked —
    everything else (Monitoring Server, VisualProcess, CameraProcess) dying
    during init is not treated as fatal here.
    """
    messages = []
    for name, proc in processes:
        if name in _CRITICAL_INIT_HINTS and not proc.is_alive():
            messages.append(
                f"{name} process exited during initialization. "
                f"{_CRITICAL_INIT_HINTS[name]}"
            )
    return messages


def check_latency_logger_alive(latency_logger) -> str | None:
    """Return a warning message if LatencyLogger died during init, or None
    if it's alive.

    Non-fatal by design -- unlike the processes in _CRITICAL_INIT_HINTS, a
    dead LatencyLogger should not abort the experiment, only lose latency
    logging for this run.
    """
    if not latency_logger.is_alive():
        return (
            "LatencyLogger process exited during initialization. "
            "Latency data will not be recorded for this run."
        )
    return None


def copy_config_to_braid_folder(config_path: str, braid_folder: str):
    """Copy config file to braid folder for record-keeping.

    Args:
        config_path: Path to the configuration file
        braid_folder: Path to the braid experiment folder
    """
    try:
        config_src = Path(config_path)
        config_dest = Path(braid_folder) / config_src.name
        with open(config_src, "rb") as src_file:
            with open(config_dest, "wb") as dest_file:
                dest_file.write(src_file.read())
        print(f"  ✓ Copied {config_src.name}")
    except Exception as e:
        print(f"  WARNING: Failed to copy {config_path}: {e}")


def handle_metadata_cancellation(braid_proxy) -> None:
    """Stop Braid recording (if this run started it) and print a clean
    cancellation message.

    Called when collect_metadata() raises UserCancelledError. This must
    run before sys.exit(0) -- collect_metadata() is called before main()'s
    experiment try/finally block begins, so nothing else in main() will
    stop a recording that check_braid_folder_exists(auto_start_recording=
    True) already started.
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

    # Parse command-line arguments
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

    # Configuration file path
    config_path = args.config

    # Load configuration
    print(f"Loading configuration from {config_path}...")
    config = load_config(config_path)
    log_level_str = config.get("logging", {}).get("level", "INFO").upper()
    log_level_int = getattr(logging, log_level_str, logging.INFO)

    recording_time_warning = check_recording_time_sufficient(config)
    if recording_time_warning:
        print(f"WARNING: {recording_time_warning}")

    # Initialize variables for cleanup
    braid_folder = None
    braid_proxy = None

    # Confirm Braid is recording BEFORE prompting for metadata — so the
    # researcher sees where data will go before filling in the form, and
    # metadata is never discarded due to a Braid connection failure.
    experiments_path = config.get("braid_publisher", {}).get(
        "experiments_path", "/mnt/data/experiments/"
    )
    braid_host = config.get("braid_publisher", {}).get("host", "127.0.0.1")
    braid_callback_port = config.get("braid_publisher", {}).get("callback_port", 12345)
    callback_url = f"http://{braid_host}:{braid_callback_port}"
    braid_folder, braid_proxy = check_braid_folder_exists(
        experiments_path, callback_url=callback_url, auto_start_recording=True
    )
    print(f"Experiment data will be saved to: {braid_folder}")

    # Collect experiment metadata (after Braid folder is confirmed)
    metadata = None
    if not args.skip_metadata:
        try:
            metadata = collect_metadata()
        except UserCancelledError:
            handle_metadata_cancellation(braid_proxy)
            sys.exit(0)
        experiment_duration = float(metadata.get("experiment_duration", 24))
    else:
        experiment_duration = 24.0
        print("⚠ Skipping metadata collection (--skip-metadata flag set)")

    # Write metadata to braid folder if it was collected
    if metadata is not None:
        write_metadata(metadata, braid_folder)
        config_columns = extract_config_columns(config_path)
        append_metadata_to_csv(metadata, braid_folder, config_columns)
    experiment_end_time = datetime.now().timestamp() + experiment_duration * 3600
    print(f"Experiment duration set to {experiment_duration} hours.")
    print(
        f"Experiment will end at: {datetime.fromtimestamp(experiment_end_time).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # Copy configuration files
    print("\nCopying configuration files...")
    copy_config_to_braid_folder(config_path, braid_folder)
    if config.get("visual_stimuli", {}).get("active", False):
        copy_config_to_braid_folder(
            config.get("visual_stimuli", {}).get(
                "config_file", "configs/visual_stimuli.toml"
            ),
            braid_folder,
        )

    # Set up file logging — all processes write to a shared log file
    log_path = str(Path(braid_folder) / "optofly.log")
    configure_process_logging(log_path, "Main", "WHITE", level=log_level_int)
    print(f"Logging to: {log_path}")

    # Create shared stop event for coordinated shutdown
    stop_event = mp.Event()

    # Track which processes to start based on config
    processes = []
    active_process_names = []

    try:
        # Core processes (always started)
        print("\nStarting core processes...")

        # 1. BraidPublisher - connects to Braid tracking and publishes to ZMQ
        print("  ✓ BraidPublisher")
        braid_publisher = BraidPublisher(
            config_path=config_path, event=stop_event, log_path=log_path,
            log_level=log_level_str,
        )
        braid_publisher.start()
        processes.append(("BraidPublisher", braid_publisher))
        active_process_names.append("BraidPublisher")
        time.sleep(0.5)  # Allow ZMQ publisher to bind

        # 2. TriggerHandler - applies spatial/temporal gating
        print("  ✓ TriggerHandler")
        trigger_handler = TriggerHandler(
            config_path=config_path, event=stop_event, log_path=log_path,
            log_level=log_level_str,
        )
        trigger_handler.start()
        processes.append(("TriggerHandler", trigger_handler))
        active_process_names.append("TriggerHandler")
        time.sleep(0.5)  # Allow ZMQ publisher to bind

        # 3. LatencyLogger - always-on, writes latency.csv. Non-critical: a
        # failure here loses latency logging for this run, not the whole
        # experiment (see check_latency_logger_alive below).
        print("  ✓ LatencyLogger")
        latency_logger = LatencyLogger(
            config_path=config_path, event=stop_event, braid_folder=braid_folder,
            log_path=log_path, log_level=log_level_str,
        )
        latency_logger.start()
        processes.append(("LatencyLogger", latency_logger))
        active_process_names.append("LatencyLogger")
        time.sleep(0.5)  # Allow ZMQ PULL socket to bind

        # Optional processes (based on config)
        print("\nStarting optional processes...")

        # 4. Monitoring Server - web dashboard for trigger visualization
        if config.get("monitoring", {}).get("active", False):
            print("  ✓ Monitoring Server")
            monitoring_config = config.get("monitoring", {})
            zmq_trigger_port = config.get("zmq", {}).get("trigger_port", 5556)
            zone_enter_topic = config.get("zmq", {}).get(
                "zone_enter_topic", "ZONE_ENTER"
            )
            monitoring_host = monitoring_config.get("host", "0.0.0.0")
            monitoring_port = monitoring_config.get("port", 5000)
            zmq_address = f"tcp://localhost:{zmq_trigger_port}"

            monitoring_process = mp.Process(
                target=run_server,
                args=(zmq_address, monitoring_host, monitoring_port, zone_enter_topic),
                daemon=True,
            )
            monitoring_process.start()
            processes.append(("Monitoring Server", monitoring_process))
            active_process_names.append("Monitoring Server")
            print(f"    Dashboard: http://{monitoring_host}:{monitoring_port}")

        # 5. VisualProcess - Panda3D visual stimuli
        if config.get("visual_stimuli", {}).get("active", False):
            print("  ✓ VisualProcess (Panda3D)")
            visual_process = VisualProcess(
                config_path=config_path,
                event=stop_event,
                braid_folder=braid_folder,
                log_path=log_path,
                log_level=log_level_str,
            )
            visual_process.start()
            processes.append(("VisualProcess", visual_process))
            active_process_names.append("VisualProcess")

        # 6. CameraProcess + LiquidLens (lens always accompanies camera)
        if config.get("camera", {}).get("active", False):
            print("  ✓ CameraProcess")
            # Save videos alongside experiment data: /mnt/data/videos/<braid_name>/
            video_folder = None
            if braid_folder:
                video_folder = str(
                    Path(braid_folder).parent.parent
                    / "videos"
                    / Path(braid_folder).name
                )
            camera = CameraProcess(
                config_path=config_path,
                event=stop_event,
                save_folder=video_folder,
                log_path=log_path,
                log_level=log_level_str,
            )
            camera.start()
            processes.append(("CameraProcess", camera))
            active_process_names.append("CameraProcess")

            print("  ✓ LiquidLens")
            liquid_lens = LiquidLens(
                event=stop_event,
                config_path=config_path,
                braid_folder=braid_folder,
                video_folder=video_folder,
                log_path=log_path,
                log_level=log_level_str,
            )
            liquid_lens.start()
            processes.append(("LiquidLens", liquid_lens))
            active_process_names.append("LiquidLens")

        # 7. OptoTriggerWorker - always started for backlight; stimulation gated by active flag
        print("  ✓ OptoTriggerWorker")
        opto_trigger = OptoTriggerWorker(
            event=stop_event,
            braid_folder=braid_folder,
            config_path=config_path,
            log_path=log_path,
            log_level=log_level_str,
        )
        opto_trigger.start()
        processes.append(("OptoTriggerWorker", opto_trigger))
        active_process_names.append("OptoTriggerWorker")

        # Allow child processes to finish their initialization
        time.sleep(1)

        # Verify critical processes are still alive after init. Each of
        # these exits immediately and unrecoverably on its own init failure
        # (bad serial port, unreachable Braid server, ZMQ bind conflict)
        # rather than retrying — catch that here rather than running a
        # silent experiment with no autofocus, no backlight, or no tracking.
        fatal_messages = check_critical_processes_alive(processes)
        if fatal_messages:
            for message in fatal_messages:
                print(f"\nFATAL: {message}")
            stop_event.set()
            sys.exit(1)

        latency_logger_warning = check_latency_logger_alive(latency_logger)
        if latency_logger_warning:
            print(f"\nWARNING: {latency_logger_warning}")

        # Print experiment summary
        print_experiment_config(config, active_process_names)

        # Wait for keyboard interrupt or experiment end time
        last_health_check = time.time()
        while not stop_event.is_set():
            if datetime.now().timestamp() >= experiment_end_time:
                print("\n\nExperiment duration reached, shutting down...")
                stop_event.set()
                break

            now = time.time()
            if now - last_health_check >= 5.0:
                fatal_messages = check_critical_processes_alive(processes)
                if fatal_messages:
                    for message in fatal_messages:
                        print(f"\nFATAL: {message} (died during the run)")
                    stop_event.set()
                    break
                last_health_check = now

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\nReceived keyboard interrupt, shutting down...")
        stop_event.set()

    except Exception as e:
        print(f"\n\nERROR during experiment: {e}")
        import traceback

        traceback.print_exc()
        stop_event.set()
        raise

    finally:
        # Graceful shutdown
        print("\nShutting down processes...")
        stop_event.set()

        # Give processes time to cleanup
        time.sleep(1)

        # Join all processes — camera needs extra time for the encoder to finish.
        _SHUTDOWN_TIMEOUTS = {"CameraProcess": 35, "RustCamera": 35}
        for name, process in processes:
            if process.is_alive():
                print(f"  Waiting for {name} to terminate...")
                timeout = _SHUTDOWN_TIMEOUTS.get(name, 5)
                process.join(timeout=timeout)
                if process.is_alive():
                    print(f"  Force terminating {name}...")
                    process.terminate()
                    process.join(timeout=2)

        # Verify CSV files are present
        if braid_folder:
            print("\nVerifying data files...")
            verify_csv_files_in_braid(braid_folder)

        # Stop Braid recording if we started it
        if braid_proxy is not None:
            print("\nStopping Braid recording...")
            try:
                braid_proxy.stop_csv_recording()
                print("✓ Recording stopped")
            except Exception as e:
                print(f"WARNING: Failed to stop recording: {e}")

        if braid_folder:
            print("\n" + "=" * 70)
            print(f"Experiment ended. Data saved to: {braid_folder}")
            print("=" * 70)
        else:
            print("\nExperiment terminated.")


if __name__ == "__main__":
    # Enable multiprocessing support for OpenGL/GUI processes
    # 'spawn' creates fresh Python interpreter instead of fork()
    # Required for OpenGL contexts to work in child processes
    mp.set_start_method("spawn", force=True)
    main()
