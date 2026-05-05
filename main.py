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
from pathlib import Path

from src.processes.braid import BraidPublisher
from src.processes.tracking import TriggerHandler
from src.visual.process import VisualProcess
from src.processes.led import OptoTriggerWorker
from src.processes.camera import RustCameraProcess as CameraProcess
from src.processes.lens import LiquidLens
from src.utils.braid import check_braid_folder_exists, copy_csv_files_to_braid
from src.utils.logger import configure_process_logging
from src.utils.metadata import collect_metadata, write_metadata, append_metadata_to_csv, extract_config_columns
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

    # Visual stimuli details
    if "VisualStimuliProcess" in active_processes:
        visual_config = config.get("visual_stimuli", {})
        enabled_stimuli = []
        if visual_config.get("background", {}).get("enabled", True):
            enabled_stimuli.append("Background (textured walls + ground)")
        if visual_config.get("looming", {}).get("enabled", False):
            expansion = visual_config["looming"].get("expansion_type", "exponential")
            enabled_stimuli.append(f"Looming disk ({expansion})")

        # Only show section if stimuli are configured
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
        kalman_enabled = lens_config.get("kalman", {}).get("enabled", False)
        predictive_enabled = lens_config.get("prediction", {}).get("enabled", False)
        print("\nLiquid Lens:")
        print(f"  Mode: {mode}")
        if kalman_enabled:
            print("  ✓ Kalman filter (predictive focus)")
        if predictive_enabled:
            print("  ✓ Trajectory prediction")

    print("\nPress Ctrl+C to stop the experiment")
    print("=" * 70 + "\n")


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


def main():
    """Launch OptoFly experiment with config-driven process selection."""

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="OptoFly experiment launcher")
    parser.add_argument(
        "--skip-metadata",
        action="store_true",
        help="Skip experiment metadata prompt (for quick tests)",
    )
    args = parser.parse_args()

    # Configuration file path
    config_path = "configs/config.toml"

    # Load configuration
    print(f"Loading configuration from {config_path}...")
    config = load_config(config_path)

    # Collect experiment metadata
    metadata = None
    if not args.skip_metadata:
        metadata = collect_metadata()
        experiment_duration = float(metadata.get("experiment_duration", 24))
    else:
        experiment_duration = 24.0
        print("⚠ Skipping metadata collection (--skip-metadata flag set)")

    # Initialize variables for cleanup
    braid_folder = None
    braid_proxy = None

    # Check for Braid recording folder or start recording if not found
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
    configure_process_logging(log_path, "Main", "WHITE")
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
            config_path=config_path, event=stop_event, log_path=log_path
        )
        braid_publisher.start()
        processes.append(("BraidPublisher", braid_publisher))
        active_process_names.append("BraidPublisher")
        time.sleep(0.5)  # Allow ZMQ publisher to bind

        # 2. TriggerHandler - applies spatial/temporal gating
        print("  ✓ TriggerHandler")
        trigger_handler = TriggerHandler(
            config_path=config_path, event=stop_event, log_path=log_path
        )
        trigger_handler.start()
        processes.append(("TriggerHandler", trigger_handler))
        active_process_names.append("TriggerHandler")
        time.sleep(0.5)  # Allow ZMQ publisher to bind

        # Optional processes (based on config)
        print("\nStarting optional processes...")

        # 3. Monitoring Server - web dashboard for trigger visualization
        if config.get("monitoring", {}).get("active", False):
            print("  ✓ Monitoring Server")
            monitoring_config = config.get("monitoring", {})
            zmq_trigger_port = config.get("zmq", {}).get("trigger_port", 5556)
            monitoring_host = monitoring_config.get("host", "0.0.0.0")
            monitoring_port = monitoring_config.get("port", 5000)
            zmq_address = f"tcp://localhost:{zmq_trigger_port}"

            monitoring_process = mp.Process(
                target=run_server,
                args=(zmq_address, monitoring_host, monitoring_port),
                daemon=True,
            )
            monitoring_process.start()
            processes.append(("Monitoring Server", monitoring_process))
            active_process_names.append("Monitoring Server")
            print(f"    Dashboard: http://{monitoring_host}:{monitoring_port}")

        # 4. VisualProcess - Panda3D visual stimuli
        if config.get("visual_stimuli", {}).get("active", False):
            print("  ✓ VisualProcess (Panda3D)")
            visual_process = VisualProcess(
                config_path=config_path,
                event=stop_event,
                braid_folder=braid_folder,
                log_path=log_path,
            )
            visual_process.start()
            processes.append(("VisualStimuliProcess", visual_process))
            active_process_names.append("VisualStimuliProcess")

        # 5. CameraProcess + LiquidLens (lens always accompanies camera)
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
            )
            liquid_lens.start()
            processes.append(("LiquidLens", liquid_lens))
            active_process_names.append("LiquidLens")

        # 6. OptoTriggerWorker - always started for backlight; stimulation gated by active flag
        print("  ✓ OptoTriggerWorker")
        opto_trigger = OptoTriggerWorker(
            event=stop_event,
            braid_folder=braid_folder,
            config_path=config_path,
            log_path=log_path,
        )
        opto_trigger.start()
        processes.append(("OptoTriggerWorker", opto_trigger))
        active_process_names.append("OptoTriggerWorker")

        # Allow child processes to finish their initialization
        time.sleep(1)

        # Print experiment summary
        print_experiment_config(config, active_process_names)

        # Wait for keyboard interrupt or experiment end time
        while not stop_event.is_set():
            if datetime.now().timestamp() >= experiment_end_time:
                print("\n\nExperiment duration reached, shutting down...")
                stop_event.set()
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

        # Join all processes
        for name, process in processes:
            if process.is_alive():
                print(f"  Waiting for {name} to terminate...")
                process.join(timeout=5)
                if process.is_alive():
                    print(f"  Force terminating {name}...")
                    process.terminate()
                    process.join(timeout=2)

        # Verify CSV files are present
        if braid_folder:
            print("\nVerifying data files...")
            copy_csv_files_to_braid(braid_folder)

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
    # Required for pyglet/OpenGL contexts to work in child processes
    mp.set_start_method("spawn", force=True)
    main()
