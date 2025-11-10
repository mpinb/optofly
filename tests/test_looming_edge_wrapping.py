#!/usr/bin/env python3
"""Test script for looming stimulus edge wrapping behavior.

This script presents looming stimuli at specific positions near screen edges
to verify that wrapping is working correctly.

Usage:
    python test_looming_edge_wrapping.py [--use-experimental-display]
"""

import argparse
import math
import time
import multiprocessing as mp
import pyglet
from src.processes.visual_stimuli import VisualStimuliProcess
from src.utils.config import ConfigBase


def create_test_trigger(heading_deg, position_offset_deg=0):
    """Create a fake trigger at a specific heading.

    Args:
        heading_deg: Fly heading in degrees (0° = North)
        position_offset_deg: Additional offset for stimulus position

    Returns:
        Dictionary mimicking a TRIGGER message
    """
    total_heading_deg = heading_deg + position_offset_deg
    return {
        "obj_id": 9999,
        "frame": int(time.time() * 240),
        "braid_timestamp": time.time(),
        "trigger_timestamp": time.time(),
        "mean_heading": math.radians(total_heading_deg),
    }


def run_edge_wrapping_test(use_experimental_display=False):
    """Run edge wrapping test with automated triggers.

    Args:
        use_experimental_display: If True, render on experimental display
    """
    print("\n" + "="*70)
    print("LOOMING STIMULUS EDGE WRAPPING TEST")
    print("="*70)

    # Update config if using experimental display
    if use_experimental_display:
        print("Configuring for experimental display...")
        config = ConfigBase("config.toml")._load_config()
        config["visual_stimuli"]["standalone"]["use_experimental_display"] = True
        # Save temporarily (or just modify in memory)

    # Test cases: (description, heading_deg, position_offset_deg)
    test_cases = [
        ("North screen center (0°)", 0, 0),
        ("Near left edge (350°)", 350, 0),
        ("Near right edge (10°)", 10, 0),
        ("East screen center (90°)", 90, 0),
        ("East-to-South boundary (135°)", 135, 0),
        ("South screen center (180°)", 180, 0),
        ("South-to-West boundary (225°)", 225, 0),
        ("West screen center (270°)", 270, 0),
        ("West-to-North boundary (315°)", 315, 0),
    ]

    print("\nTest cases:")
    for i, (desc, heading, offset) in enumerate(test_cases, 1):
        print(f"  {i}. {desc}")

    print("\nPress number keys 1-9 to trigger each test case")
    print("Press ESC to exit\n")

    # Initialize visual stimuli process
    stop_event = mp.Event()
    process = VisualStimuliProcess(
        config_path="config.toml",
        event=stop_event,
        log_level="INFO",
        standalone=True
    )

    if not process.initialize():
        print("Failed to initialize visual stimuli process")
        return

    # Store test cases in a dictionary for easy access
    test_trigger_map = {}
    for i, (desc, heading, offset) in enumerate(test_cases, 1):
        test_trigger_map[i] = (desc, heading, offset)

    # Add keyboard handler
    @process.window.event
    def on_key_press(symbol, modifiers):
        # Check for number keys 1-9
        if pyglet.window.key._1 <= symbol <= pyglet.window.key._9:
            key_num = symbol - pyglet.window.key._0
            if key_num in test_trigger_map:
                desc, heading, offset = test_trigger_map[key_num]
                print(f"\nTriggering: {desc}")
                print(f"  Heading: {heading}°, Offset: {offset}°, Total: {heading + offset}°")

                # Create and send trigger
                trigger_data = create_test_trigger(heading, offset)
                process.registry.on_trigger(trigger_data)

                # Print expected pixel position for debugging
                total_rad = math.radians(heading + offset)
                expected_pixel = (total_rad / (2 * math.pi)) * process.geometry.screen_width
                print(f"  Expected pixel: {expected_pixel:.1f} (screen_width={process.geometry.screen_width})")

                return True

        elif symbol == pyglet.window.key.ESCAPE:
            print("\nExiting...")
            pyglet.app.exit()
            return True

        elif symbol == pyglet.window.key.SPACE:
            print("\nPress a number key (1-9) to trigger a specific test case")
            return True

    # Schedule render loop
    target_fps = process.config.get("target_fps", 240)
    pyglet.clock.schedule_interval(process._render_loop, 1.0 / target_fps)

    # Run
    print("Test ready. Window should be open.")
    print("Press number keys 1-9 to test different positions.\n")

    try:
        pyglet.app.run()
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        process._cleanup()
        stop_event.set()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test looming stimulus edge wrapping behavior"
    )
    parser.add_argument(
        "--use-experimental-display",
        action="store_true",
        help="Render on experimental display instead of small test window"
    )

    args = parser.parse_args()

    run_edge_wrapping_test(args.use_experimental_display)
