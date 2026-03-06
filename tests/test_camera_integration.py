#!/usr/bin/env python3
"""
Integration test for the Ximea camera system.

This script tests the full integration between Python and Rust:
1. Pre-flight checks
2. Camera process startup
3. ZMQ trigger reception
4. Video file creation
"""

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import zmq

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.processes.ximea_camera import CameraProcess, check_camera_prerequisites
from src.utils.config import CameraConfig


def test_prerequisites(config_path: str = "configs/config.toml") -> bool:
    """
    Test pre-flight checks.

    Args:
        config_path: Path to configuration file

    Returns:
        True if all checks pass
    """
    print("\n" + "=" * 60)
    print("TEST 1: Pre-Flight Checks")
    print("=" * 60)

    results = check_camera_prerequisites(config_path)

    print(f"\nResults:")
    print(f"  Rust Binary: {'✓' if results['rust_binary'] else '✗'}")
    print(f"  FFmpeg:      {'✓' if results['ffmpeg'] else '✗'}")
    print(f"  Save Folder: {'✓' if results['save_folder'] else '✗'}")
    print(f"  ZMQ Port:    {'✓' if results['zmq_port'] else '✗'}")
    print(f"  Overall:     {'✓' if results['overall'] else '✗'}")

    if results["warnings"]:
        print(f"\nWarnings:")
        for warning in results["warnings"]:
            print(f"  - {warning}")

    if results["errors"]:
        print(f"\nErrors:")
        for error in results["errors"]:
            print(f"  - {error}")

    return results["overall"]


def test_trigger_simulation(config_path: str = "configs/config.toml", duration: float = 5.0):
    """
    Test camera response to simulated triggers.

    Args:
        config_path: Path to configuration file
        duration: How long to run the test in seconds
    """
    print("\n" + "=" * 60)
    print("TEST 2: Trigger Simulation")
    print("=" * 60)

    config = CameraConfig(config_path)

    # Create ZMQ publisher to simulate trigger_handler
    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    publisher_address = f"tcp://{config.zmq_address}:{config.zmq_port}"

    print(f"\nBinding test publisher to {publisher_address}")
    publisher.bind(publisher_address)
    time.sleep(1)  # Let socket settle

    # Start camera process
    print("Starting camera process...")
    stop_event = mp.Event()
    camera = CameraProcess(
        config_path=config_path,
        event=stop_event,
        log_level="DEBUG",
    )

    if not camera.initialize():
        print("✗ Camera initialization failed")
        return False

    camera.start()
    print("✓ Camera process started")

    # Wait for camera to be ready
    time.sleep(2)

    # Send test triggers
    print(f"\nSending test triggers for {duration}s...")
    start_time = time.time()
    trigger_count = 0

    try:
        while time.time() - start_time < duration:
            # Send a trigger message
            trigger_msg = {
                "obj_id": 999,
                "frame": trigger_count * 100,
            }
            message = json.dumps(trigger_msg)
            publisher.send_string(f"TRIGGER {message}")
            trigger_count += 1
            print(f"  Sent trigger {trigger_count}: obj_id=999, frame={trigger_count * 100}")

            # Wait between triggers
            time.sleep(1.5)

    except KeyboardInterrupt:
        print("\n✗ Test interrupted by user")

    # Send kill signal
    print("\nSending kill signal to camera...")
    publisher.send_string("kill")
    time.sleep(1)

    # Stop camera process
    stop_event.set()
    camera.join(timeout=5)

    # Clean up
    publisher.close()
    context.term()

    print(f"\n✓ Sent {trigger_count} triggers successfully")

    # Check for output files
    print("\nChecking for output files...")
    save_path = Path(config.save_folder)
    if not save_path.is_absolute():
        config_dir = Path(config_path).parent
        save_path = (config_dir / save_path).resolve()

    video_files = list(save_path.glob("obj_id_999_*.mp4"))
    csv_files = list(save_path.glob("obj_id_999_*.csv"))

    print(f"  Found {len(video_files)} video files")
    print(f"  Found {len(csv_files)} CSV files")

    if video_files:
        print("\n✓ Video files created:")
        for f in video_files[:3]:  # Show first 3
            size_mb = f.stat().st_size / 1_000_000
            print(f"    {f.name} ({size_mb:.1f} MB)")
        if len(video_files) > 3:
            print(f"    ... and {len(video_files) - 3} more")
    else:
        print("\n✗ No video files created")

    return len(video_files) > 0


def main():
    """Run integration tests."""
    print("\n" + "=" * 60)
    print("XIMEA CAMERA INTEGRATION TESTS")
    print("=" * 60)

    config_path = "configs/config.toml"

    # Test 1: Pre-flight checks
    if not test_prerequisites(config_path):
        print("\n✗ Pre-flight checks failed. Fix errors before continuing.")
        return 1

    print("\n✓ Pre-flight checks passed")

    # Test 2: Ask user if they want to run trigger simulation
    print("\n" + "=" * 60)
    print("Ready to run trigger simulation test.")
    print("This will:")
    print("  1. Start the camera process")
    print("  2. Send simulated TRIGGER messages")
    print("  3. Record short video clips")
    print("=" * 60)

    response = input("\nRun trigger simulation test? (y/N): ").strip().lower()
    if response != 'y':
        print("\nSkipping trigger simulation test")
        return 0

    # Test 2: Trigger simulation
    success = test_trigger_simulation(config_path, duration=5.0)

    if success:
        print("\n" + "=" * 60)
        print("✓ ALL TESTS PASSED")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("✗ TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
