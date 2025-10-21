"""Calibration modes for visual stimuli system."""

import json
import numpy as np
import pyglet
import zmq
from typing import List, Tuple


def run_screen_identification(
    window_width: int = 7680,
    window_height: int = 1080,
    window_x_offset: int = 3840
) -> None:
    """Run screen identification calibration mode.

    Displays labels on each screen quadrant for physical identification.

    Args:
        window_width: Total display width
        window_height: Display height
        window_x_offset: X offset for window positioning
    """
    print("=== Screen Identification Calibration ===")
    print("This will display labels on each screen.")
    print("Identify which screen is North/East/South/West.")
    print("Press ESC to exit.")
    print()

    # Create window
    window = pyglet.window.Window(
        width=window_width,
        height=window_height,
        caption="Screen Identification Calibration"
    )
    window.set_location(window_x_offset, 0)

    # Create labels for each screen quadrant
    screen_labels = []
    screen_names = ["DP-0.1", "DP-0.2", "DP-2.1", "DP-2.2"]

    for i, name in enumerate(screen_names):
        x_center = (i * 1920) + 960  # Center of each 1920px screen
        y_center = window_height // 2

        label = pyglet.text.Label(
            f"Screen {i+1}\n{name}",
            font_name='Arial',
            font_size=72,
            x=x_center,
            y=y_center,
            anchor_x='center',
            anchor_y='center',
            multiline=True,
            width=1920
        )
        screen_labels.append(label)

    # Instructions at top
    instructions = pyglet.text.Label(
        "Identify which screen is North/East/South/West, then update config.toml",
        font_name='Arial',
        font_size=36,
        x=window_width // 2,
        y=window_height - 100,
        anchor_x='center',
        anchor_y='center'
    )

    @window.event
    def on_draw():
        window.clear()
        for label in screen_labels:
            label.draw()
        instructions.draw()

    @window.event
    def on_key_press(symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            pyglet.app.exit()

    print("Displaying screen labels...")
    print("Update config.toml with screen_mapping after identification.")
    print()

    pyglet.app.run()


def run_heading_calibration(
    window_width: int = 7680,
    window_height: int = 1080,
    window_x_offset: int = 3840,
    zmq_port: int = 5555,
    zmq_topic: str = "BRAID",
    num_calibration_points: int = 12,
    output_file: str = "calibrations/heading_mapping_data.csv"
) -> None:
    """Run heading-to-pixel empirical calibration mode.

    Displays calibration circles and records Braid positions.

    Args:
        window_width: Total display width
        window_height: Display height
        window_x_offset: X offset for window
        zmq_port: ZMQ port for BRAID messages
        zmq_topic: ZMQ topic for BRAID messages
        num_calibration_points: Number of calibration circles
        output_file: CSV file to save calibration data
    """
    print("=== Heading-to-Pixel Calibration ===")
    print(f"This will display {num_calibration_points} calibration circles.")
    print("For each circle:")
    print("  1. Position object in arena directly facing the circle")
    print("  2. Press SPACE to record the Braid position")
    print("  3. Move to next circle")
    print()
    print("Press ESC to cancel and exit.")
    print()

    # Calculate calibration circle positions
    calibration_x_positions = np.linspace(
        0, window_width - 1, num_calibration_points, dtype=int
    )

    # Create window
    window = pyglet.window.Window(
        width=window_width,
        height=window_height,
        caption="Heading Calibration"
    )
    window.set_location(window_x_offset, 0)

    # Setup ZMQ subscriber
    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)
    subscriber.connect(f"tcp://localhost:{zmq_port}")
    subscriber.setsockopt_string(zmq.SUBSCRIBE, zmq_topic)
    print(f"Connected to BRAID messages on port {zmq_port}")

    # Calibration state
    current_point_idx = 0
    calibration_data = []
    current_braid_position = None

    # Create circle and label
    circle_y = window_height // 2
    circle_radius = 50

    def get_current_circle():
        x = calibration_x_positions[current_point_idx]
        return pyglet.shapes.Circle(
            x=x, y=circle_y, radius=circle_radius,
            color=(255, 0, 0)  # Red
        )

    def get_current_label():
        x = calibration_x_positions[current_point_idx]
        return pyglet.text.Label(
            f"Point {current_point_idx + 1}/{num_calibration_points}\nPixel X: {x}\n\nPress SPACE to record",
            font_name='Arial',
            font_size=24,
            x=x,
            y=circle_y + 150,
            anchor_x='center',
            anchor_y='center',
            multiline=True,
            width=400
        )

    circle = get_current_circle()
    label = get_current_label()

    @window.event
    def on_draw():
        window.clear()
        circle.draw()
        label.draw()

    @window.event
    def on_key_press(symbol, modifiers):
        nonlocal current_point_idx, circle, label

        if symbol == pyglet.window.key.ESCAPE:
            print("\nCalibration cancelled.")
            pyglet.app.exit()
            return

        if symbol == pyglet.window.key.SPACE:
            # Record current position
            if current_braid_position is None:
                print("  No Braid data available, waiting...")
                return

            x_pos = calibration_x_positions[current_point_idx]
            braid_x, braid_y = current_braid_position

            calibration_data.append({
                "pixel_x": x_pos,
                "braid_x": braid_x,
                "braid_y": braid_y
            })

            print(f"  Recorded: pixel_x={x_pos}, braid_x={braid_x:.4f}, braid_y={braid_y:.4f}")

            # Move to next point
            current_point_idx += 1

            if current_point_idx >= num_calibration_points:
                # Calibration complete
                print("\nCalibration complete!")
                save_calibration_data(calibration_data, output_file)
                pyglet.app.exit()
            else:
                # Update circle and label for next point
                circle = get_current_circle()
                label = get_current_label()
                print(f"\nMove to point {current_point_idx + 1}/{num_calibration_points}")

    def update_braid_position(dt):
        """Poll for latest Braid position."""
        nonlocal current_braid_position

        try:
            if subscriber.poll(timeout=0):
                topic, message = subscriber.recv_multipart(zmq.NOBLOCK)
                message_str = message.decode("utf-8")
                data = json.loads(message_str)

                # Extract position from Birth or Update message
                if "Birth" in data:
                    pos_data = data["Birth"]
                elif "Update" in data:
                    pos_data = data["Update"]
                else:
                    return

                current_braid_position = (pos_data["x"], pos_data["y"])

        except (zmq.Again, json.JSONDecodeError, KeyError):
            pass

    # Schedule Braid position updates
    pyglet.clock.schedule_interval(update_braid_position, 0.1)  # Poll at 10Hz

    print(f"Starting calibration with {num_calibration_points} points...")
    print(f"Position object facing point 1/{num_calibration_points}")

    pyglet.app.run()

    # Cleanup
    subscriber.close()
    context.term()


def save_calibration_data(
    calibration_data: List[dict],
    output_file: str
) -> None:
    """Save calibration data to CSV and generate interpolation model.

    Args:
        calibration_data: List of dicts with pixel_x, braid_x, braid_y
        output_file: Path to save CSV data
    """
    import os
    import pandas as pd
    from scipy.interpolate import interp1d

    # Create calibrations directory if needed
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    # Save to CSV
    df = pd.DataFrame(calibration_data)
    df.to_csv(output_file, index=False)
    print(f"\nCalibration data saved to: {output_file}")

    # Calculate headings
    headings = np.arctan2(df["braid_y"], df["braid_x"])
    pixels = df["pixel_x"].values

    # Sort by heading
    sorted_indices = np.argsort(headings)
    headings_sorted = headings.values[sorted_indices]
    pixels_sorted = pixels[sorted_indices]

    # Save interpolation model
    model_file = output_file.replace("_data.csv", "_model.npz")
    np.savez(model_file, headings=headings_sorted, pixels=pixels_sorted)
    print(f"Interpolation model saved to: {model_file}")
    print()
    print("Update config.toml:")
    print(f'  calibration_mapping_file = "{model_file}"')
    print('  use_empirical_calibration = true')
