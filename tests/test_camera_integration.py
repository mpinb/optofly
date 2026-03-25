#!/usr/bin/env python3
"""
Integration test for the Ximea camera system.

This script tests the full integration:
1. Pre-flight checks (ximea-py, PyAV, save folder, ZMQ)
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

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.processes.camera import CameraProcess, check_camera_prerequisites
from src.utils.config import CameraConfig


def test_prerequisites(config_path: str = "configs/config.toml") -> bool:
    """
    Test pre-flight checks.

    Returns:
        True if all checks pass
    """
    print("\n" + "=" * 60)
    print("TEST 1: Pre-Flight Checks")
    print("=" * 60)

    results = check_camera_prerequisites(config_path)

    print("\nResults:")
    print(f"  ximea-py:    {'OK' if results['ximea'] else 'FAIL'}")
    print(f"  PyAV:        {'OK' if results['pyav'] else 'FAIL'}")
    print(f"  Save Folder: {'OK' if results['save_folder'] else 'FAIL'}")
    print(f"  ZMQ Port:    {'OK' if results['zmq_port'] else 'FAIL'}")
    print(f"  Overall:     {'OK' if results['overall'] else 'FAIL'}")

    if results["warnings"]:
        print("\nWarnings:")
        for warning in results["warnings"]:
            print(f"  - {warning}")

    if results["errors"]:
        print("\nErrors:")
        for error in results["errors"]:
            print(f"  - {error}")

    return results["overall"]


def test_trigger_simulation(
    config_path: str = "configs/config.toml", duration: float = 5.0
):
    """
    Test camera response to simulated triggers.
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
    time.sleep(1)

    # Start camera process
    print("Starting camera process...")
    stop_event = mp.Event()
    camera = CameraProcess(
        config_path=config_path,
        event=stop_event,
        log_level="DEBUG",
    )

    camera.start()
    print("Camera process started")

    # Wait for camera to be ready
    time.sleep(2)

    # Send test triggers
    print(f"\nSending test triggers for {duration}s...")
    start_time = time.time()
    trigger_count = 0

    try:
        while time.time() - start_time < duration:
            trigger_msg = {
                "obj_id": 999,
                "frame": trigger_count * 100,
            }
            message = json.dumps(trigger_msg)
            publisher.send_multipart([b"TRIGGER", message.encode()])
            trigger_count += 1
            print(
                f"  Sent trigger {trigger_count}: obj_id=999, frame={trigger_count * 100}"
            )
            time.sleep(1.5)

    except KeyboardInterrupt:
        print("\nTest interrupted by user")

    # Stop camera process
    publisher.send_multipart([b"kill", b""])
    time.sleep(1)
    stop_event.set()
    camera.join(timeout=5)

    # Clean up
    publisher.close()
    context.term()

    print(f"\nSent {trigger_count} triggers successfully")

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
        print("\nVideo files created:")
        for f in video_files[:3]:
            size_mb = f.stat().st_size / 1_000_000
            print(f"    {f.name} ({size_mb:.1f} MB)")
        if len(video_files) > 3:
            print(f"    ... and {len(video_files) - 3} more")
    else:
        print("\nNo video files created")

    return len(video_files) > 0


def main():
    """Run integration tests."""
    print("\n" + "=" * 60)
    print("XIMEA CAMERA INTEGRATION TESTS")
    print("=" * 60)

    config_path = "configs/config.toml"

    if not test_prerequisites(config_path):
        print("\nPre-flight checks failed. Fix errors before continuing.")
        return 1

    print("\nPre-flight checks passed")

    print("\n" + "=" * 60)
    print("Ready to run trigger simulation test.")
    print("This will:")
    print("  1. Start the camera process")
    print("  2. Send simulated TRIGGER messages")
    print("  3. Record short video clips")
    print("=" * 60)

    response = input("\nRun trigger simulation test? (y/N): ").strip().lower()
    if response != "y":
        print("\nSkipping trigger simulation test")
        return 0

    success = test_trigger_simulation(config_path, duration=5.0)

    if success:
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
