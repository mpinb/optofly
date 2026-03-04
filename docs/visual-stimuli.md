# Visual Stimuli

Developer guide for the visual stimulus rendering system.

## Overview

The visual stimuli package provides a 240Hz, plugin-based system for rendering visual patterns on a multi-screen display. It integrates with OptoFly's closed-loop tracking system to present stimuli in response to fly behavior.

## Architecture

```
VisualStimuliProcess (main process)
    |-- DisplayManager          # Creates/manages pyglet window
    |-- GeometryUtils           # Coordinate conversions
    |-- StimulusRegistry        # Manages all active stimuli
    |   |-- StaticPattern       # Open-loop stimulus
    |   |-- LoomingStimulus     # Closed-loop stimulus
    |   `-- YourCustomStimulus
    |-- pyglet.graphics.Batch   # Rendering container (single GPU call)
    `-- CSVWriter               # Logs stimulus events
```

**Initialization (once at startup):**
1. Create pyglet window and Batch
2. Initialize all stimuli (load config)
3. Call `stimulus.initialize_rendering(batch)` for each
4. Start 240Hz rendering loop

**Per-frame loop (240x/second):**
1. Check for TRIGGER messages from ZMQ
2. If TRIGGER received → `StimulusRegistry.on_trigger(data)`
3. `StimulusRegistry.update_all(dt)` → update stimulus state
4. `StimulusRegistry.render_all(batch)` → update shapes in batch
5. `window.on_draw()` → `batch.draw()` → GPU renders

**Key classes:**

| Class | File | Purpose |
|-------|------|---------|
| `BaseStimulus` | `base_stimulus.py` | Abstract interface |
| `StimulusRegistry` | `stimulus_registry.py` | Manages plugins, dispatches events |
| `GeometryUtils` | `geometry_utils.py` | Heading-to-pixels, degrees-to-pixels |
| `DisplayManager` | `display_manager.py` | Creates pyglet window |
| `VisualStimuliProcess` | `../processes/visual.py` | Main process loop |

## Core Concepts

### The Pyglet Batch

All shapes are added to a single `pyglet.graphics.Batch`, which renders them in one GPU call. Creating new shape objects every frame is slow — always update properties in-place:

```python
# Slow: recreates object each frame
self.circle = pyglet.shapes.Circle(x, y, radius, batch=batch)

# Fast: updates existing object
self.circle.x = x
self.circle.radius = radius
```

### Initialize vs. Render

- `initialize_rendering(batch)` — called once at startup; create static shapes here
- `render(batch)` — called 240x/second; update shape properties here

### Coordinate Systems

- **Braid space**: position (x, y, z) in meters, heading = `arctan2(yvel, xvel)`, origin at arena center
- **Display space**: (0, 0) at bottom-left, (7680, 1080) at top-right, four 1920x1080 screens, cylindrical wrap
- **Stimulus space**: degrees relative to fly heading (0° = directly ahead, -90° = left, +90° = right)

Use `GeometryUtils` for all conversions:

```python
pixel_x = geometry.heading_to_pixel_x(fly_heading_rad=1.57, offset_deg=90.0)
radius_px = geometry.degrees_to_pixels(angular_size_deg=20.0)
center_y = geometry.get_vertical_center()  # 540
```

### Open-Loop vs. Closed-Loop

- **Open-loop**: always displayed, `is_active()` always returns True (e.g., static pattern)
- **Closed-loop**: displayed only in response to triggers, uses a state machine (e.g., looming)

### State Machines

Closed-loop stimuli use a state machine:

```
IDLE --[on_trigger()]--> ACTIVE --[time > duration]--> COOLDOWN --[time > cooldown]--> IDLE
```

`update(dt)` advances the state; `render()` draws the current state.

## Included Stimuli

### StaticPatternStimulus

Open-loop. Random binary pattern (QR-code-like) rendered as a single sprite.

```toml
[visual_stimuli.static]
enabled = true
square_color = "black"
background_color = "white"
pattern_density = 0.3       # Fraction of black pixels
downscale_factor = 2
random_seed = 42
```

### LoomingStimulusRenderer

Closed-loop. Expanding circle simulating an approaching predator.

```toml
[visual_stimuli.looming]
enabled = true
initial_size_deg = [5.0, 10.0, 15.0]   # Randomized per trigger
final_size_deg = 72.0
expansion_duration_ms = [300, 500, 700]
positions_deg = [-90, 0, 90]            # Balanced presentation
```

## Creating a New Stimulus

### Step 1: Create the stimulus class

Create `src/stimuli/my_stimulus.py`:

```python
from src.stimuli.base_stimulus import BaseStimulus
from src.stimuli.geometry_utils import GeometryUtils
import pyglet.shapes

class MyStimulus(BaseStimulus):

    IDLE = 0
    ACTIVE = 1

    def __init__(self, config, geometry_utils, logger, csv_writer):
        super().__init__(config)
        self.geometry = geometry_utils
        self.logger = logger
        self.enabled = config.get("enabled", True)
        self.state = self.IDLE
        self.shape = None

    def initialize_rendering(self, batch):
        self._batch = batch

    def on_trigger(self, trigger_data):
        if self.state != self.IDLE:
            return
        self.state = self.ACTIVE
        self.elapsed = 0.0

    def update(self, dt):
        if self.state == self.ACTIVE:
            self.elapsed += dt
            if self.elapsed > 1.0:
                self.state = self.IDLE

    def render(self, batch):
        if self.state == self.IDLE:
            if self.shape:
                self.shape.delete()
                self.shape = None
            return
        if self.shape is None:
            self.shape = pyglet.shapes.Circle(
                x=3840, y=540, radius=100, batch=batch
            )

    def is_active(self):
        return self.enabled and self.state != self.IDLE

    def cleanup(self):
        if self.shape:
            self.shape.delete()
            self.shape = None
```

### Step 2: Register in VisualStimuliProcess

In `src/processes/visual.py`, in `_initialize_stimuli()`:

```python
my_config = self.config.get("my_stimulus", {})
if my_config.get("enabled", False):
    from src.stimuli.my_stimulus import MyStimulus
    stim = MyStimulus(my_config, self.geometry, self.logger, self.csv_writer)
    self.registry.register("my_stimulus", stim)
```

### Step 3: Add config

In `visual_stimuli.toml`:

```toml
[visual_stimuli.my_stimulus]
enabled = true
```

## Advanced Topics

### Randomized Parameters

Parse config values as lists and randomly select at trigger time:

```python
def __init__(self, config):
    val = config.get("speed", 1000.0)
    self.speed_options = val if isinstance(val, list) else [val]

def on_trigger(self, trigger_data):
    self.speed = np.random.choice(self.speed_options)
```

### Position Balancing

Ensure equal presentation across positions:

```python
def __init__(self, config):
    self.positions_deg = config.get("positions_deg", [-90, 0, 90])
    self.position_counts = {p: 0 for p in self.positions_deg}

def _select_balanced_position(self):
    min_count = min(self.position_counts.values())
    candidates = [p for p, c in self.position_counts.items() if c == min_count]
    selected = np.random.choice(candidates)
    self.position_counts[selected] += 1
    return selected
```

### Edge Wrapping (Cylindrical Display)

For stimuli that extend past screen edges:

```python
def render(self, batch):
    # ... create/update main shape ...
    if self.center_x - self.radius < 0 or self.center_x + self.radius > 7680:
        wrapped_x = self.center_x + 7680 if self.center_x < 3840 else self.center_x - 7680
        if self.wrapped_shape is None:
            self.wrapped_shape = pyglet.shapes.Circle(wrapped_x, self.center_y, self.radius, batch=batch)
        else:
            self.wrapped_shape.x = wrapped_x
    elif self.wrapped_shape:
        self.wrapped_shape.delete()
        self.wrapped_shape = None
```

### Performance Tips

- Pre-calculate constants in `__init__()`, not `update()` or `render()`
- Keep shape count low (>1000 shapes may reduce FPS)
- Use rectangles instead of circles when possible (faster rendering)
- Delete shapes when not in use: `shape.delete(); shape = None`

## BaseStimulus Interface

```python
class BaseStimulus(ABC):
    @abstractmethod
    def render(self, batch):        # Required: add/update shapes (240x/sec)
        pass

    @abstractmethod
    def is_active(self) -> bool:    # Required: whether to render
        pass

    @abstractmethod
    def cleanup(self):              # Required: delete shapes, free resources
        pass

    def update(self, dt):           # Optional: advance state (240x/sec)
        pass

    def on_trigger(self, data):     # Optional: handle TRIGGER messages
        pass

    def initialize_rendering(self, batch):  # Optional: one-time setup
        pass
```

## Trigger Message Format

```python
{
    "obj_id": 1,
    "frame": 12345,
    "braid_timestamp": 123456.789,
    "trigger_timestamp": 123456.790,
    "mean_heading": 1.57            # radians
}
```

## Troubleshooting

**Stimulus not appearing:**
- Check `enabled = true` in config
- Look for `"MyStimulus registered"` in startup logs
- Verify `is_active()` returns True when expected
- Ensure shapes are created with `batch=batch` parameter

**Wrong position:**
- Use `GeometryUtils` for all coordinate conversions
- Pyglet origin is bottom-left (not top-left)

**FPS drops below 240Hz:**
- Look for log warnings: `Performance: 180.2 fps`
- Check for shape recreation each frame instead of in-place updates
- Profile with `cProfile` to find bottlenecks
