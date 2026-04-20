"""Calibration modes for visual stimuli system."""

import signal
import numpy as np
import pyglet
from typing import List


def run_screen_identification(
    window_width: int = 7680, window_height: int = 1080, window_x_offset: int = 3840
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
        caption="Screen Identification Calibration",
    )
    window.set_location(window_x_offset, 0)

    # Create labels for each screen quadrant
    screen_labels = []
    screen_names = ["DP-0.1", "DP-0.2", "DP-2.1", "DP-2.2"]

    for i, name in enumerate(screen_names):
        x_center = (i * 1920) + 1920  # Center of each 1920px screen
        y_center = window_height // 2

        label = pyglet.text.Label(
            f"Screen {i + 1}\n{name}",
            font_name="Arial",
            font_size=72,
            x=x_center,
            y=y_center,
            anchor_x="center",
            anchor_y="center",
            multiline=True,
            width=1920,
        )
        screen_labels.append(label)

    # Instructions at top
    instructions = pyglet.text.Label(
        "Identify which screen is North/East/South/West, then update configs/config.toml",
        font_name="Arial",
        font_size=36,
        x=window_width // 2,
        y=window_height - 100,
        anchor_x="center",
        anchor_y="center",
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

    # Handle Ctrl+C gracefully
    def sigint_handler(signum, frame):
        print("\nScreen identification interrupted (Ctrl+C).")
        pyglet.app.exit()

    signal.signal(signal.SIGINT, sigint_handler)

    print("Displaying screen labels...")
    print("Update configs/config.toml with screen_mapping after identification.")
    print()

    pyglet.app.run()


def run_heading_calibration(
    window_width: int = 7680,
    window_height: int = 1080,
    window_x_offset: int = 3840,
    num_calibration_points: int = 4,
    screen_order: List[int] = None,
    output_file: str = "calibrations/heading_mapping_data.csv",
) -> None:
    """Run heading-to-pixel empirical calibration mode with manual input.

    Displays calibration circles and prompts user to enter Braid positions manually.

    Args:
        window_width: Total display width
        window_height: Display height
        window_x_offset: X offset for window
        num_calibration_points: Number of calibration circles (minimum 4)
        screen_order: Physical clockwise order of screens by index.
                      Default [0, 1, 2, 3] for standard clockwise setup.
                      Pixel layout: screen 0 (0-1919), 1 (1920-3839), 2 (3840-5759), 3 (5760-7679)
        output_file: CSV file to save calibration data
    """
    # Default screen order: natural pixel order (assumes cables are arranged clockwise)
    if screen_order is None:
        screen_order = [0, 1, 2, 3]

    print("=== Heading-to-Pixel Calibration (Manual Mode) ===")
    print(f"This will display {num_calibration_points} calibration circles.")
    print(f"Screen order (clockwise): {screen_order}")
    print()
    print("For each circle:")
    print("  1. Position object in arena directly facing the circle")
    print("  2. Enter the x,y position from Braid in the terminal")
    print("  3. Press ENTER to record and continue")
    print()
    print("Press Ctrl+C to cancel.")
    print()

    # Calculate calibration circle positions
    num_screens = 4
    screen_width = window_width // num_screens
    screen_centers = [screen_width * i + screen_width // 2 for i in range(num_screens)]

    # Reorder screen centers based on physical clockwise order
    ordered_screen_centers = [screen_centers[i] for i in screen_order]

    # If more than 4 calibration points, interpolate additional points
    if num_calibration_points <= num_screens:
        calibration_x_positions = np.array(
            ordered_screen_centers[:num_calibration_points]
        )
    else:
        calibration_x_positions = []
        points_per_screen = num_calibration_points // num_screens
        extra_points = num_calibration_points % num_screens

        for i, screen_idx in enumerate(screen_order):
            screen_start = screen_idx * screen_width
            n_points = points_per_screen + (1 if i < extra_points else 0)
            for j in range(n_points):
                x = screen_start + int(screen_width * (j + 0.5) / n_points)
                calibration_x_positions.append(x)

        calibration_x_positions = np.array(calibration_x_positions)

    # Screen direction labels (matching configs/visual_stimuli.toml screen_mapping order)
    screen_directions = ["West", "North", "East", "South"]  # After cable swap

    def get_screen_for_pixel(pixel_x):
        return min(pixel_x // screen_width, num_screens - 1)

    def get_direction_for_pixel(pixel_x):
        screen_idx = get_screen_for_pixel(pixel_x)
        return screen_directions[screen_idx]

    # Create window
    window = pyglet.window.Window(
        width=window_width, height=window_height, caption="Heading Calibration"
    )
    window.set_location(window_x_offset, 0)

    # Graphics objects
    circle_y = window_height // 2
    circle_radius = 50
    batch = pyglet.graphics.Batch()

    calibration_data = []

    try:
        for point_idx in range(num_calibration_points):
            x_pos = calibration_x_positions[point_idx]
            direction = get_direction_for_pixel(x_pos)

            # Create graphics for this point
            circle = pyglet.shapes.Circle(
                x=x_pos,
                y=circle_y,
                radius=circle_radius,
                color=(255, 0, 0),
                batch=batch,
            )

            label = pyglet.text.Label(
                f"Point {point_idx + 1}/{num_calibration_points} ({direction})\nPixel X: {x_pos}",
                font_name="Arial",
                font_size=24,
                x=x_pos,
                y=circle_y + 150,
                anchor_x="center",
                anchor_y="center",
                multiline=True,
                width=500,
                batch=batch,
            )

            status_label = pyglet.text.Label(
                "Enter coordinates in terminal",
                font_name="Arial",
                font_size=18,
                x=window_width // 2,
                y=50,
                anchor_x="center",
                anchor_y="center",
                color=(255, 255, 0, 255),
                batch=batch,
            )

            # Render the display
            window.switch_to()
            window.clear()
            batch.draw()
            window.flip()

            # Process any pending events to keep window responsive
            window.dispatch_events()

            # Prompt for input in terminal
            print(
                f"\n--- Point {point_idx + 1}/{num_calibration_points} ({direction}, Pixel X: {x_pos}) ---"
            )
            print(
                "Position object facing the red circle, then enter Braid coordinates."
            )

            while True:
                try:
                    braid_x = float(input("  Braid X: "))
                    braid_y = float(input("  Braid Y: "))
                    break
                except ValueError:
                    print("  Invalid input. Please enter numeric values.")

            calibration_data.append(
                {"pixel_x": x_pos, "braid_x": braid_x, "braid_y": braid_y}
            )

            print(
                f"  Recorded: pixel_x={x_pos}, braid_x={braid_x:.4f}, braid_y={braid_y:.4f}"
            )

            # Clean up graphics for this point
            circle.delete()
            label.delete()
            status_label.delete()

        # Calibration complete
        print("\nCalibration complete!")
        save_calibration_data(calibration_data, output_file)

    except KeyboardInterrupt:
        print("\n\nCalibration cancelled.")

    finally:
        window.close()


def save_calibration_data(calibration_data: List[dict], output_file: str) -> None:
    """Save calibration data to CSV and generate interpolation model.

    Args:
        calibration_data: List of dicts with pixel_x, braid_x, braid_y
        output_file: Path to save CSV data
    """
    import os
    import pandas as pd

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
    print("Update configs/config.toml:")
    print(f'  calibration_mapping_file = "{model_file}"')
    print("  use_empirical_calibration = true")


def run_calibration_test(
    window_width: int = 7680,
    window_height: int = 1080,
    window_x_offset: int = 3840,
    sweep_speed_deg_per_sec: float = 45.0,
) -> None:
    """Run a visual test to verify screen arrangement and wrapping.

    Displays a circle that sweeps continuously around the display clockwise.
    Shows screen boundaries and direction labels.

    Controls:
        SPACE: Pause/resume sweep
        LEFT/RIGHT: Manual adjustment when paused
        R: Reset to 0°
        ESC: Exit

    Args:
        window_width: Total display width
        window_height: Display height
        window_x_offset: X offset for window
        sweep_speed_deg_per_sec: Speed of sweep in degrees per second
    """
    print("=== Calibration Test Mode ===")
    print("A circle will sweep clockwise around the display.")
    print()
    print("Controls:")
    print("  SPACE: Pause/resume sweep")
    print("  LEFT/RIGHT: Adjust position manually (when paused)")
    print("  R: Reset to 0° (West)")
    print("  ESC: Exit")
    print()

    # Create window
    window = pyglet.window.Window(
        width=window_width, height=window_height, caption="Calibration Test"
    )
    window.set_location(window_x_offset, 0)

    # Screen setup
    num_screens = 4
    screen_width = window_width // num_screens
    screen_directions = ["West", "North", "East", "South"]

    # State
    current_angle_deg = [0.0]  # Use list to allow modification in nested function
    paused = [False]

    # Graphics
    batch = pyglet.graphics.Batch()
    circle_y = window_height // 2
    circle_radius = 80

    # Create screen boundary lines and labels
    boundary_lines = []
    direction_labels = []

    for i in range(num_screens):
        # Boundary line at start of each screen (use Rectangle as thin line)
        x = i * screen_width
        line = pyglet.shapes.Rectangle(
            x=x, y=0, width=2, height=window_height, color=(100, 100, 100), batch=batch
        )
        boundary_lines.append(line)

        # Direction label at center of each screen
        label_x = i * screen_width + screen_width // 2
        label = pyglet.text.Label(
            screen_directions[i],
            font_name="Arial",
            font_size=48,
            x=label_x,
            y=window_height - 80,
            anchor_x="center",
            anchor_y="center",
            color=(150, 150, 150, 255),
            batch=batch,
        )
        direction_labels.append(label)

    # Moving circle
    circle = pyglet.shapes.Circle(
        x=0, y=circle_y, radius=circle_radius, color=(255, 0, 0), batch=batch
    )

    # Info labels
    angle_label = pyglet.text.Label(
        "Angle: 0.0°",
        font_name="Arial",
        font_size=24,
        x=20,
        y=window_height - 30,
        color=(255, 255, 255, 255),
        batch=batch,
    )

    pixel_label = pyglet.text.Label(
        "Pixel X: 0",
        font_name="Arial",
        font_size=24,
        x=20,
        y=window_height - 60,
        color=(255, 255, 255, 255),
        batch=batch,
    )

    status_label = pyglet.text.Label(
        "[SPACE] Pause | [←/→] Adjust | [R] Reset | [ESC] Exit",
        font_name="Arial",
        font_size=16,
        x=window_width // 2,
        y=30,
        anchor_x="center",
        color=(200, 200, 200, 255),
        batch=batch,
    )

    pause_label = pyglet.text.Label(
        "",
        font_name="Arial",
        font_size=36,
        x=window_width // 2,
        y=window_height // 2 + 150,
        anchor_x="center",
        color=(255, 255, 0, 255),
        batch=batch,
    )

    def angle_to_pixel_x(angle_deg):
        """Convert angle (0-360) to pixel X position.

        0° = West (left edge), 90° = North, 180° = East, 270° = South
        """
        # Map 0-360 to 0-window_width
        return int((angle_deg / 360.0) * window_width) % window_width

    def update_display(dt=0):
        """Update circle position and labels."""
        if not paused[0]:
            current_angle_deg[0] = (
                current_angle_deg[0] + sweep_speed_deg_per_sec * dt
            ) % 360

        pixel_x = angle_to_pixel_x(current_angle_deg[0])
        circle.x = pixel_x
        angle_label.text = f"Angle: {current_angle_deg[0]:.1f}°"
        pixel_label.text = f"Pixel X: {pixel_x}"
        pause_label.text = "PAUSED" if paused[0] else ""

    @window.event
    def on_draw():
        window.clear()
        batch.draw()

    @window.event
    def on_key_press(symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            pyglet.app.exit()
        elif symbol == pyglet.window.key.SPACE:
            paused[0] = not paused[0]
            update_display(0)
        elif symbol == pyglet.window.key.LEFT and paused[0]:
            current_angle_deg[0] = (current_angle_deg[0] - 5) % 360
            update_display(0)
        elif symbol == pyglet.window.key.RIGHT and paused[0]:
            current_angle_deg[0] = (current_angle_deg[0] + 5) % 360
            update_display(0)
        elif symbol == pyglet.window.key.R:
            current_angle_deg[0] = 0.0
            update_display(0)

    # Initialize display
    update_display(0)

    # Schedule updates at 60fps
    pyglet.clock.schedule_interval(update_display, 1 / 60.0)

    # Handle Ctrl+C
    def sigint_handler(signum, frame):
        print("\nTest interrupted.")
        pyglet.app.exit()

    signal.signal(signal.SIGINT, sigint_handler)

    print("Starting test...")
    pyglet.app.run()


def run_mapping_test(
    calibration_file: str = "calibrations/heading_mapping_model.npz",
    window_width: int = 7680,
    window_height: int = 1080,
    window_x_offset: int = 3840,
) -> None:
    """Test the heading-to-pixel calibration mapping.

    Lets you enter Braid x,y coordinates and shows where they map on screen.

    Args:
        calibration_file: Path to the calibration model (.npz)
        window_width: Total display width
        window_height: Display height
        window_x_offset: X offset for window
    """
    import os

    print("=== Calibration Mapping Test ===")
    print(f"Loading calibration from: {calibration_file}")

    if not os.path.exists(calibration_file):
        print(f"ERROR: Calibration file not found: {calibration_file}")
        print("Run --calibrate-mapping first to create the calibration.")
        return

    # Load calibration model
    data = np.load(calibration_file)
    cal_headings = data["headings"]
    cal_pixels = data["pixels"]

    print(f"Loaded {len(cal_headings)} calibration points")
    print()
    print("Enter Braid x,y coordinates to see where they map on screen.")
    print("The circle will appear at the corresponding pixel position.")
    print()
    print("Controls:")
    print("  Enter coordinates in terminal, then press ENTER")
    print("  Type 'q' to quit")
    print()

    # Create window
    window = pyglet.window.Window(
        width=window_width, height=window_height, caption="Mapping Test"
    )
    window.set_location(window_x_offset, 0)

    # Screen setup
    num_screens = 4
    screen_width = window_width // num_screens
    screen_directions = ["West", "North", "East", "South"]

    # Graphics
    batch = pyglet.graphics.Batch()
    circle_y = window_height // 2
    circle_radius = 80

    # Screen boundary lines and labels
    for i in range(num_screens):
        pyglet.shapes.Rectangle(
            x=i * screen_width,
            y=0,
            width=2,
            height=window_height,
            color=(100, 100, 100),
            batch=batch,
        )
        pyglet.text.Label(
            screen_directions[i],
            font_name="Arial",
            font_size=48,
            x=i * screen_width + screen_width // 2,
            y=window_height - 80,
            anchor_x="center",
            anchor_y="center",
            color=(150, 150, 150, 255),
            batch=batch,
        )

    # Marker circle
    circle = pyglet.shapes.Circle(
        x=window_width // 2,
        y=circle_y,
        radius=circle_radius,
        color=(0, 255, 0),
        batch=batch,
    )

    # Info labels
    heading_label = pyglet.text.Label(
        "Heading: --",
        font_name="Arial",
        font_size=24,
        x=20,
        y=window_height - 30,
        color=(255, 255, 255, 255),
        batch=batch,
    )

    pixel_label = pyglet.text.Label(
        "Pixel X: --",
        font_name="Arial",
        font_size=24,
        x=20,
        y=window_height - 60,
        color=(255, 255, 255, 255),
        batch=batch,
    )

    braid_label = pyglet.text.Label(
        "Braid: --",
        font_name="Arial",
        font_size=24,
        x=20,
        y=window_height - 90,
        color=(255, 255, 255, 255),
        batch=batch,
    )

    status_label = pyglet.text.Label(
        "Enter Braid coordinates in terminal",
        font_name="Arial",
        font_size=18,
        x=window_width // 2,
        y=30,
        anchor_x="center",
        color=(255, 255, 0, 255),
        batch=batch,
    )

    def heading_to_pixel(heading_rad):
        """Convert heading to pixel using calibration interpolation."""
        # Linear interpolation using calibration data
        return int(np.interp(heading_rad, cal_headings, cal_pixels, period=2 * np.pi))

    def update_marker(braid_x, braid_y):
        """Update marker position based on Braid coordinates."""
        heading_rad = np.arctan2(braid_y, braid_x)
        heading_deg = np.degrees(heading_rad)
        pixel_x = heading_to_pixel(heading_rad)

        circle.x = pixel_x
        heading_label.text = f"Heading: {heading_deg:.1f}° ({heading_rad:.3f} rad)"
        pixel_label.text = f"Pixel X: {pixel_x}"
        braid_label.text = f"Braid: x={braid_x:.4f}, y={braid_y:.4f}"

    @window.event
    def on_draw():
        window.clear()
        batch.draw()

    # Flag to control the input loop
    running = [True]

    def input_loop():
        """Run input loop in separate thread-like manner using pyglet scheduling."""
        while running[0]:
            try:
                # Dispatch events to keep window responsive
                window.dispatch_events()

                print("\n" + "-" * 40)
                braid_input = input("Braid X (or 'q' to quit): ").strip()

                if braid_input.lower() == "q":
                    running[0] = False
                    pyglet.app.exit()
                    break

                braid_x = float(braid_input)
                braid_y = float(input("Braid Y: ").strip())

                update_marker(braid_x, braid_y)

                # Redraw
                window.switch_to()
                window.clear()
                batch.draw()
                window.flip()

                print(f"  → Mapped to pixel X: {circle.x}")

            except ValueError:
                print("  Invalid input. Enter numeric values.")
            except KeyboardInterrupt:
                running[0] = False
                break
            except Exception as e:
                print(f"  Error: {e}")
                running[0] = False
                break

        window.close()

    # Run input loop directly (not using pyglet.app.run)
    print("Ready for input...")
    input_loop()
