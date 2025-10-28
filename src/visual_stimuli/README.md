# Visual Stimuli Package

Comprehensive guide to the OptoFly visual stimulus rendering system.

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Core Concepts](#core-concepts)
4. [Included Stimuli](#included-stimuli)
5. [Creating a New Stimulus (Step-by-Step)](#creating-a-new-stimulus-step-by-step)
6. [Advanced Topics](#advanced-topics)
7. [Troubleshooting](#troubleshooting)

## Overview

The visual stimuli package provides a high-performance (240Hz), plugin-based system for rendering visual patterns on multi-screen displays. It integrates with OptoFly's closed-loop tracking system to present stimuli in response to fly behavior.

**Key Features:**
- 240Hz refresh rate for smooth motion
- Closed-loop: responds to real-time tracking data
- Hardware-accelerated rendering via Pyglet
- Automatic coordinate conversion (fly heading → screen pixels)
- CSV logging of all stimulus presentations
- Easy extensibility via plugin architecture

## Architecture

### Component Overview

```
VisualStimuliProcess (main process)
    ├── DisplayManager          # Creates/manages pyglet window
    ├── GeometryUtils           # Coordinate conversions
    ├── StimulusRegistry        # Manages all active stimuli
    │   ├── StaticPattern       # Example open-loop stimulus
    │   ├── LoomingStimulus     # Example closed-loop stimulus
    │   └── YourCustomStimulus  # Your new stimulus!
    ├── pyglet.graphics.Batch   # Rendering container
    └── CSVWriter               # Logs stimulus events
```

### Data Flow

**Initialization (once at startup):**
```
1. VisualStimuliProcess.initialize()
2. Create pyglet window and Batch
3. Initialize all stimuli (load config)
4. Call stimulus.initialize_rendering(batch) for each stimulus
5. Start 240Hz rendering loop
```

**Per-Frame Loop (240 times/second):**
```
1. Check for TRIGGER messages from ZMQ
2. If TRIGGER received → StimulusRegistry.on_trigger(data)
3. StimulusRegistry.update_all(dt) → update stimulus state
4. StimulusRegistry.render_all(batch) → update shapes in batch
5. window.on_draw() → batch.draw() → GPU renders everything
```

### Key Classes

| Class | File | Purpose |
|-------|------|---------|
| `BaseStimulus` | `base_stimulus.py` | Abstract interface all stimuli inherit from |
| `StimulusRegistry` | `stimulus_registry.py` | Manages stimulus plugins, dispatches events |
| `GeometryUtils` | `geometry_utils.py` | Converts heading→pixels, degrees→pixels |
| `DisplayManager` | `display_manager.py` | Creates pyglet window |
| `VisualStimuliProcess` | `../processes/visual_stimuli.py` | Main process loop |

## Core Concepts

### 1. The Pyglet Batch

**What is it?**
A `pyglet.graphics.Batch` is a container that holds multiple drawable shapes (circles, rectangles, lines, etc.) and renders them all with a single GPU call.

**Why use it?**
Rendering 500 individual shapes would require 500 separate GPU calls. Rendering them all in one batch requires just 1 GPU call. This is essential for hitting 240Hz.

**How does it work?**
```python
# Create batch (once)
batch = pyglet.graphics.Batch()

# Add shapes to batch (pass batch parameter)
circle = pyglet.shapes.Circle(x=100, y=100, radius=50, batch=batch)
rect = pyglet.shapes.Rectangle(x=200, y=200, width=100, height=100, batch=batch)

# Later: draw everything in one call
batch.draw()  # Renders both circle and rect efficiently
```

**Important:** Shapes remain in the batch until you delete them. You update their properties (x, y, radius) in-place rather than recreating them each frame.

### 2. Initialize vs. Render

Stimuli have two methods for working with graphics:

**`initialize_rendering(batch)`** - Called ONCE during startup
- Use for: Creating static shapes, storing batch reference
- Example: Static pattern adds its sprite to batch here
- Why: Avoids creating objects every frame

**`render(batch)`** - Called 240 TIMES PER SECOND
- Use for: Updating shape properties, creating/hiding dynamic shapes
- Example: Looming stimulus updates circle.radius here
- Why: Keeps display in sync with current stimulus state

### 3. Coordinate Systems

OptoFly uses multiple coordinate systems:

**Braid Tracking Space:**
- Fly position: (x, y, z) in meters
- Fly heading: radians, calculated as `arctan2(yvel, xvel)`
- Origin: center of arena

**Display Space:**
- Pixels: (0, 0) = bottom-left, (7680, 1080) = top-right
- Four 1920×1080 screens arranged horizontally
- Wraps around cylindrically (left edge connects to right edge)

**Stimulus Space:**
- Degrees relative to fly heading
- 0° = directly ahead of fly
- -90° = to fly's left, +90° = to fly's right

**Converting Between Systems:**

Use `GeometryUtils` for conversions:

```python
# Convert fly heading + offset to screen x-coordinate
pixel_x = geometry.heading_to_pixel_x(
    fly_heading_rad=1.57,  # Fly facing "up" in arena
    offset_deg=90.0         # Show stimulus 90° to fly's right
)
# Result: pixel_x = 3840 (right side of display)

# Convert angular size to pixel radius
radius_px = geometry.degrees_to_pixels(
    angular_size_deg=20.0   # 20° diameter circle
)
# Result: radius_px ≈ 200 pixels (depends on viewing distance)
```

### 4. Open-Loop vs. Closed-Loop Stimuli

**Open-Loop Stimuli:**
- Always displayed, independent of fly behavior
- Example: Static background pattern
- `is_active()` returns `True` constantly
- `on_trigger()` not used

**Closed-Loop Stimuli:**
- Displayed only in response to fly behavior
- Example: Looming stimulus when fly enters trigger zone
- `is_active()` returns `True` only during presentation
- `on_trigger()` starts the presentation

### 5. State Machines for Closed-Loop Stimuli

Closed-loop stimuli typically use a state machine:

```python
# States
IDLE = 0       # Not presenting, waiting for trigger
ACTIVE = 1     # Currently presenting stimulus
COOLDOWN = 2   # Finished, but can't trigger again yet

# State transitions
IDLE --[on_trigger()]--> ACTIVE
ACTIVE --[time > duration]--> COOLDOWN
COOLDOWN --[time > cooldown_period]--> IDLE
```

The `update(dt)` method advances the state based on elapsed time.

## Included Stimuli

### StaticPatternStimulus

Random binary pattern resembling a QR code, generated from numpy matrix.

**Features:**
- Configurable pattern density (0.0 to 1.0)
- Configurable downscaling for performance tuning
- Reproducible patterns via random seed
- Single sprite (1 draw call) for optimal performance

**Configuration:**
```toml
[visual_stimuli.static]
enabled = true
square_color = "black"              # Pattern pixel color
background_color = "white"          # Background color
pattern_density = 0.3               # Probability of pattern pixels (0.0-1.0)
downscale_factor = 2                # 1=full res, 2=half, 4=quarter
random_seed = 42                    # Optional: reproducible patterns
```

**Performance:**
- Memory: ~24MB texture (all downscale factors upscale to full res)
- Rendering: 1 sprite vs old 500 rectangles
- FPS: ≥230 fps maintained or improved

**Migration from old version:**
- `num_squares` → replaced by `pattern_density`
- `average_square_size_px` → no longer used
- `square_size_std_px` → no longer used
- Rule of thumb: `density ≈ (num_squares × size²) / (7680 × 1080)`

### LoomingStimulusRenderer

**Type:** Closed-loop
**Purpose:** Expanding circle simulating approaching predator

**Implementation highlights:**
```python
# State machine
IDLE = 0
EXPANDING = 1
HOLDING = 2

def on_trigger(self, trigger_data):
    # Select position with balanced presentation
    position_deg = self._select_balanced_position()

    # Randomly select parameters (if randomized)
    self.initial_size_deg = np.random.choice(self.initial_size_deg_options)

    # Calculate screen position from fly heading
    self.center_x = self.geometry.heading_to_pixel_x(
        trigger_data['mean_heading'],
        position_deg
    )

    # Start expansion
    self.state = self.EXPANDING
    self.elapsed_time = 0.0

def update(self, dt):
    if self.state == EXPANDING:
        self.elapsed_time += dt
        # Update radius based on L/V equation
        self.current_radius_px = self._calculate_lv_radius(self.elapsed_time)

        if self.elapsed_time >= self.expansion_duration_ms / 1000.0:
            self.state = self.HOLDING

def render(self, batch):
    if self.state == self.IDLE:
        self._hide_circles()
        return

    # Create circle if needed, otherwise update in-place
    if self.circle is None:
        self.circle = pyglet.shapes.Circle(
            x=self.center_x,
            y=self.center_y,
            radius=self.current_radius_px,
            batch=batch
        )
    else:
        # Update existing circle (no recreation!)
        self.circle.radius = self.current_radius_px
```

**Key insights:**
- State machine tracks IDLE → EXPANDING → HOLDING transitions
- `on_trigger()` sets up presentation parameters
- `update()` advances time and calculates new radius
- `render()` creates circle on first call, then updates it
- Edge wrapping handled by creating second "wrapped" circle when needed

## Creating a New Stimulus (Step-by-Step)

This tutorial walks through creating a simple "moving bar" stimulus that sweeps across the screen.

### Step 1: Create the Stimulus File

Create `src/visual_stimuli/moving_bar.py`:

```python
"""Moving bar stimulus for motion detection experiments."""

import time
import numpy as np
import pyglet.shapes
from typing import Dict, Any
from src.visual_stimuli.base_stimulus import BaseStimulus
from src.visual_stimuli.geometry_utils import GeometryUtils


class MovingBarStimulus(BaseStimulus):
    """Vertical bar that sweeps across the display.

    Closed-loop stimulus triggered by fly tracking.
    """

    # State machine
    IDLE = 0
    MOVING = 1

    def __init__(
        self,
        config: Dict[str, Any],
        geometry_utils: GeometryUtils,
        logger,
        csv_writer
    ):
        """Initialize moving bar stimulus.

        Args:
            config: Configuration from [visual_stimuli.moving_bar] section
            geometry_utils: GeometryUtils instance
            logger: Logger instance
            csv_writer: CSVWriter instance
        """
        super().__init__(config)
        self.geometry = geometry_utils
        self.logger = logger
        self.csv_writer = csv_writer

        # Parse configuration
        self.enabled = config.get("enabled", True)
        self.bar_width_px = config.get("bar_width_px", 100)
        self.bar_color = self._parse_color(config.get("bar_color", "black"))
        self.speed_px_per_sec = config.get("speed_px_per_sec", 1000.0)
        self.direction = config.get("direction", "left_to_right")  # or "right_to_left"

        # State
        self.state = self.IDLE
        self.current_x = 0
        self.bar_height = self.geometry.screen_height

        # Rendering
        self.bar_rect = None

        # Trigger data for logging
        self.trigger_data = None

    def initialize_rendering(self, batch: pyglet.graphics.Batch) -> None:
        """Store batch reference (bar created on-demand when triggered).

        Args:
            batch: Pyglet graphics batch
        """
        self._batch_ref = batch

    def on_trigger(self, trigger_data: Dict[str, Any]) -> None:
        """Start bar movement in response to trigger.

        Args:
            trigger_data: Trigger data from trigger_handler
        """
        if self.state != self.IDLE:
            self.logger.warning(
                f"MovingBar trigger ignored - already active "
                f"(obj_id={trigger_data['obj_id']})"
            )
            return

        # Set starting position based on direction
        if self.direction == "left_to_right":
            self.current_x = 0
        else:
            self.current_x = self.geometry.screen_width

        # Start movement
        self.state = self.MOVING
        self.trigger_data = trigger_data

        # Log event
        self._log_stimulus_event(trigger_data)

        self.logger.info(
            f"MovingBar started: obj_id={trigger_data['obj_id']}, "
            f"direction={self.direction}"
        )

    def update(self, dt: float) -> None:
        """Update bar position.

        Args:
            dt: Time since last frame (seconds)
        """
        if self.state != self.MOVING:
            return

        # Update position
        if self.direction == "left_to_right":
            self.current_x += self.speed_px_per_sec * dt
            # Check if bar moved off screen
            if self.current_x > self.geometry.screen_width:
                self.state = self.IDLE
                self.bar_rect = None
        else:
            self.current_x -= self.speed_px_per_sec * dt
            # Check if bar moved off screen
            if self.current_x < -self.bar_width_px:
                self.state = self.IDLE
                self.bar_rect = None

    def render(self, batch: pyglet.graphics.Batch) -> None:
        """Create or update bar rectangle.

        Args:
            batch: Pyglet graphics batch
        """
        if self.state == self.IDLE:
            self._hide_bar()
            return

        # Create or update bar
        if self.bar_rect is None:
            self.bar_rect = pyglet.shapes.Rectangle(
                x=self.current_x,
                y=0,
                width=self.bar_width_px,
                height=self.bar_height,
                color=self.bar_color,
                batch=batch
            )
        else:
            # Update position
            self.bar_rect.x = self.current_x

    def is_active(self) -> bool:
        """Return True if stimulus should be rendered.

        Returns:
            True if enabled and currently moving
        """
        return self.enabled and self.state == self.MOVING

    def cleanup(self) -> None:
        """Clean up resources."""
        self._hide_bar()

    def _hide_bar(self) -> None:
        """Remove bar from batch."""
        if self.bar_rect is not None:
            self.bar_rect.delete()
            self.bar_rect = None

    def _log_stimulus_event(self, trigger_data: Dict[str, Any]) -> None:
        """Log stimulus parameters to CSV.

        Args:
            trigger_data: Trigger message data
        """
        log_data = {
            "timestamp": time.time(),
            "obj_id": trigger_data["obj_id"],
            "frame": trigger_data["frame"],
            "stimulus_type": "moving_bar",
            "bar_width_px": self.bar_width_px,
            "speed_px_per_sec": self.speed_px_per_sec,
            "direction": self.direction,
            "bar_color": str(self.bar_color)
        }
        self.csv_writer.append(log_data)

    def _parse_color(self, color) -> tuple:
        """Convert string or RGB list to RGB tuple.

        Args:
            color: Color name or RGB list/tuple

        Returns:
            RGB tuple
        """
        if isinstance(color, str):
            color_map = {
                "black": (0, 0, 0),
                "white": (255, 255, 255),
                "red": (255, 0, 0),
                "green": (0, 255, 0),
                "blue": (0, 0, 255)
            }
            return color_map.get(color.lower(), (0, 0, 0))
        else:
            return tuple(color[:3])
```

### Step 2: Add Configuration Section

Add to `config.toml`:

```toml
[visual_stimuli.moving_bar]
enabled = true
bar_width_px = 100
bar_color = "black"
speed_px_per_sec = 1000.0  # 1000 pixels/sec = ~1.3 seconds to cross screen
direction = "left_to_right"  # or "right_to_left"
```

### Step 3: Register in VisualStimuliProcess

Edit `src/processes/visual_stimuli.py`, in the `_initialize_stimuli()` method:

```python
def _initialize_stimuli(self) -> None:
    """Initialize and register enabled stimuli."""

    # ... existing static pattern code ...

    # ... existing looming stimulus code ...

    # Register moving bar if enabled
    moving_bar_config = self.config.get("moving_bar", {})
    if moving_bar_config.get("enabled", False):
        from src.visual_stimuli.moving_bar import MovingBarStimulus

        moving_bar = MovingBarStimulus(
            config=moving_bar_config,
            geometry_utils=self.geometry,
            logger=self.logger,
            csv_writer=self.csv_writer
        )
        self.registry.register("moving_bar", moving_bar)
        self.logger.info("Moving bar stimulus registered")

    # Initialize rendering after all stimuli registered
    self.registry.initialize_all_rendering(self.batch)
    self.logger.info("Stimulus rendering initialized")
```

### Step 4: Test Your Stimulus

Run the visual stimuli process and trigger it:

```python
from src.processes.visual_stimuli import VisualStimuliProcess
import multiprocessing as mp

stop_event = mp.Event()
visual = VisualStimuliProcess(config_path="config.toml", event=stop_event)

if visual.initialize():
    visual.start()

    # Simulate a trigger (in real system, comes from trigger_handler)
    import zmq
    import json

    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    publisher.bind("tcp://*:5556")

    trigger_data = {
        "obj_id": 1,
        "frame": 1000,
        "braid_timestamp": 123456.789,
        "trigger_timestamp": 123456.790,
        "mean_heading": 0.0  # Facing right
    }

    publisher.send_multipart([
        b"TRIGGER",
        json.dumps(trigger_data).encode('utf-8')
    ])

    # Watch the bar sweep across the screen!
```

## Advanced Topics

### Randomized Parameters

Allow experimenters to randomize parameters across presentations:

```python
def __init__(self, config):
    # Parse parameter that can be single value or list
    self.speed_options = self._parse_parameter(
        config.get("speed_px_per_sec", 1000.0),
        "speed_px_per_sec"
    )

def _parse_parameter(self, value, param_name: str):
    """Convert single value or list to list of options."""
    if isinstance(value, list):
        if len(value) == 0:
            raise ValueError(f"{param_name} cannot be empty list")
        return value
    else:
        return [value]  # Single value becomes single-item list

def on_trigger(self, trigger_data):
    # Randomly select from options
    self.speed_px_per_sec = np.random.choice(self.speed_options)
    # ... rest of trigger handling ...
```

**Config:**
```toml
[visual_stimuli.moving_bar]
speed_px_per_sec = [500.0, 1000.0, 1500.0]  # Randomly select each time
```

### Position Balancing

Ensure equal presentation across multiple positions:

```python
def __init__(self, config):
    self.positions_deg = config.get("positions_deg", [-90, 0, 90])
    self.position_counts = {pos: 0 for pos in self.positions_deg}

def _select_balanced_position(self) -> float:
    """Select position with least usage."""
    min_count = min(self.position_counts.values())
    candidates = [
        pos for pos, count in self.position_counts.items()
        if count == min_count
    ]
    selected = np.random.choice(candidates)
    self.position_counts[selected] += 1
    return selected

def on_trigger(self, trigger_data):
    position_deg = self._select_balanced_position()
    # Use position_deg to calculate screen location...
```

### Edge Wrapping (Cylindrical Display)

Handle stimuli that extend beyond screen edges:

```python
def render(self, batch):
    # ... create main circle ...

    # Check if circle crosses edge
    if self._needs_wrapping():
        wrapped_x = self._get_wrapped_x()
        if self.wrapped_circle is None:
            self.wrapped_circle = pyglet.shapes.Circle(
                x=wrapped_x,
                y=self.center_y,
                radius=self.radius,
                batch=batch
            )
        else:
            self.wrapped_circle.x = wrapped_x
            self.wrapped_circle.radius = self.radius
    else:
        self._hide_wrapped_circle()

def _needs_wrapping(self) -> bool:
    """Check if stimulus crosses display edge."""
    return (
        (self.center_x - self.radius < 0) or
        (self.center_x + self.radius > self.geometry.screen_width)
    )

def _get_wrapped_x(self) -> int:
    """Calculate wrapped x-coordinate."""
    if self.center_x - self.radius < 0:
        # Wraps off left → show on right
        return self.center_x + self.geometry.screen_width
    else:
        # Wraps off right → show on left
        return self.center_x - self.geometry.screen_width
```

### Performance Optimization Tips

1. **Reuse shapes**: Update properties instead of deleting/recreating
   ```python
   # SLOW (creates new object each frame)
   self.circle = pyglet.shapes.Circle(x, y, radius, batch=batch)

   # FAST (updates existing object)
   self.circle.x = x
   self.circle.y = y
   self.circle.radius = radius
   ```

2. **Use batch rendering**: Always pass `batch` parameter to shapes
   ```python
   # Creates shape and adds to batch in one step
   circle = pyglet.shapes.Circle(x, y, radius, batch=batch)
   ```

3. **Minimize per-frame work**: Pre-calculate constants in `__init__()`
   ```python
   # SLOW (calculates every frame)
   def update(self, dt):
       self.angle = (self.angle + self.speed_deg_per_sec * dt) % 360
       self.x = math.cos(math.radians(self.angle)) * self.radius

   # FAST (pre-calculate conversion factor)
   def __init__(self, config):
       self.speed_rad_per_sec = math.radians(
           config.get("speed_deg_per_sec", 45.0)
       )

   def update(self, dt):
       self.angle = (self.angle + self.speed_rad_per_sec * dt) % (2 * math.pi)
       self.x = math.cos(self.angle) * self.radius
   ```

4. **Delete unused shapes**: Remove from batch when not needed
   ```python
   def _hide_circle(self):
       if self.circle is not None:
           self.circle.delete()  # Removes from batch
           self.circle = None
   ```

### Custom Coordinate Transformations

For stimuli that need custom positioning logic:

```python
def _calculate_position_relative_to_fly(
    self,
    fly_heading_rad: float,
    offset_deg: float,
    distance_from_fly_deg: float
) -> tuple[int, int]:
    """Calculate position for stimulus at specific angle/distance from fly.

    Args:
        fly_heading_rad: Fly heading in radians
        offset_deg: Angular offset from fly heading
        distance_from_fly_deg: Angular distance from fly position

    Returns:
        (pixel_x, pixel_y) tuple
    """
    # Convert fly heading to pixel x
    base_x = self.geometry.heading_to_pixel_x(fly_heading_rad, offset_deg)

    # Calculate y offset from center based on distance
    # (positive distance = above center, negative = below)
    pixel_offset_y = self.geometry.degrees_to_pixels(distance_from_fly_deg)
    pixel_y = self.geometry.get_vertical_center() + pixel_offset_y

    return (base_x, pixel_y)
```

## Troubleshooting

### Stimulus Not Appearing

**Check 1: Is it enabled?**
```toml
[visual_stimuli.my_stimulus]
enabled = true  # ← Must be true!
```

**Check 2: Is it registered?**
Look for log message: `"MyStimulus registered"` at startup

**Check 3: Is `is_active()` returning True?**
```python
def is_active(self) -> bool:
    return self.enabled and self.state != self.IDLE
```

**Check 4: Are shapes added to batch?**
```python
# WRONG: Missing batch parameter
circle = pyglet.shapes.Circle(x, y, radius)

# RIGHT: Includes batch
circle = pyglet.shapes.Circle(x, y, radius, batch=batch)
```

### Shapes Appear at Wrong Position

**Check 1: Coordinate conversion**
```python
# Use GeometryUtils for conversions
pixel_x = self.geometry.heading_to_pixel_x(heading_rad, offset_deg)

# Don't manually calculate (may be wrong!)
pixel_x = (heading_rad / (2 * math.pi)) * screen_width  # WRONG!
```

**Check 2: Origin is bottom-left**
Pyglet uses bottom-left as (0, 0), not top-left!

### Performance Issues (< 240Hz)

**Check logs:** Look for warnings like:
```
WARNING - Performance: 180.2 fps (avg: 5.55ms, max: 8.12ms)
```

**Common causes:**
1. Creating new shapes every frame instead of updating
2. Too many shapes in scene (>1000 may slow down)
3. Complex math in `update()` or `render()`

**Solutions:**
- Profile with `cProfile` to find bottlenecks
- Pre-calculate values in `__init__()`
- Reduce number of shapes (larger squares instead of many small ones)
- Use simpler shapes (rectangles faster than circles)

### Triggers Not Received

**Check 1: ZMQ connection**
```bash
# Check if trigger_handler is publishing
netstat -tulpn | grep 5556
```

**Check 2: Topic subscription**
Make sure trigger_handler publishes on topic "TRIGGER" (default)

**Check 3: Log messages**
Look for: `"Received TRIGGER message: obj_id=X"`

### Shapes Not Wrapping at Edges

Check wrapping logic:
```python
def _needs_wrapping(self) -> bool:
    """Check if shape extends beyond screen edges."""
    return (
        (self.x - self.width/2 < 0) or  # Left edge
        (self.x + self.width/2 > self.geometry.screen_width)  # Right edge
    )
```

## Reference

### BaseStimulus Abstract Interface

All stimuli must implement these methods:

```python
class BaseStimulus(ABC):

    @abstractmethod
    def render(self, batch: pyglet.graphics.Batch) -> None:
        """Add/update shapes in batch (called 240x/sec)."""
        pass

    def update(self, dt: float) -> None:
        """Update state based on time (called 240x/sec)."""
        pass

    def on_trigger(self, trigger_data: Dict[str, Any]) -> None:
        """Handle TRIGGER messages (closed-loop stimuli only)."""
        pass

    @abstractmethod
    def is_active(self) -> bool:
        """Return True if stimulus should be rendered."""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Delete shapes and free resources."""
        pass

    def initialize_rendering(self, batch: pyglet.graphics.Batch) -> None:
        """Optional: one-time setup (static shapes, batch storage)."""
        pass
```

### Pyglet Shapes Quick Reference

```python
# Circle
circle = pyglet.shapes.Circle(
    x=100, y=100,           # Center position
    radius=50,              # Radius in pixels
    color=(255, 0, 0),      # RGB color (0-255)
    batch=batch             # Add to batch
)
circle.x = 200              # Update position
circle.radius = 75          # Update size
circle.delete()             # Remove from batch

# Rectangle
rect = pyglet.shapes.Rectangle(
    x=100, y=100,           # Bottom-left corner
    width=200, height=100,  # Dimensions
    color=(0, 255, 0),
    batch=batch
)

# Line
line = pyglet.shapes.Line(
    x=0, y=0, x2=100, y2=100,  # Start and end points
    width=5,                    # Line thickness
    color=(0, 0, 255),
    batch=batch
)

# Arc (partial circle)
arc = pyglet.shapes.Arc(
    x=100, y=100,
    radius=50,
    angle=0,                # Start angle (radians)
    angle_end=math.pi/2,    # End angle (radians)
    color=(255, 255, 0),
    batch=batch
)
```

### GeometryUtils Methods

```python
# Convert fly heading + angular offset to screen x-coordinate
pixel_x = geometry.heading_to_pixel_x(
    braid_heading_rad=1.57,  # Fly heading (radians)
    offset_deg=90.0          # Offset from heading (degrees)
)

# Convert angular size to pixel radius
radius_px = geometry.degrees_to_pixels(
    angular_size_deg=20.0    # Angular size (degrees)
)

# Get vertical center of display
center_y = geometry.get_vertical_center()  # Usually 540 (1080/2)

# Properties
screen_width = geometry.screen_width    # 7680
screen_height = geometry.screen_height  # 1080
```

### Trigger Message Format

Trigger messages contain:
```python
{
    "obj_id": 1,                    # Braid object ID
    "frame": 12345,                 # Camera frame number
    "braid_timestamp": 123456.789,  # Braid timestamp
    "trigger_timestamp": 123456.790, # TriggerHandler timestamp
    "mean_heading": 1.57            # Fly heading (radians)
}
```

---

**Questions?** Check the source code examples in `looming_stimulus.py` and `static_pattern.py` for complete working implementations.
