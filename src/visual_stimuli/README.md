# Visual Stimuli Package

This package implements visual stimulus rendering for OptoFly's closed-loop experiments.

## Architecture

Plugin-based stimulus system with 240Hz pyglet rendering:

- **BaseStimulus**: Abstract interface for all stimuli
- **StimulusRegistry**: Manages active stimulus plugins
- **GeometryUtils**: Coordinate conversions (heading→pixels, degrees→pixels)
- **DisplayManager**: Pyglet window management
- **Calibration**: Interactive calibration modes

## Included Stimuli

### StaticPatternStimulus
Random QR-code-like background pattern. Open-loop, always displayed.

### LoomingStimulusRenderer
Expanding circle with L/V ratio dynamics. Closed-loop, triggered by fly tracking.

## Adding a New Stimulus

1. **Create file:** `src/visual_stimuli/my_stimulus.py`

2. **Inherit from BaseStimulus:**

```python
from src.visual_stimuli.base_stimulus import BaseStimulus
import pyglet.shapes

class MyStimulus(BaseStimulus):
    def __init__(self, config):
        super().__init__(config)
        self.enabled = config.get("enabled", True)
        # Load other config parameters

    def render(self, batch):
        if not self.is_active():
            return
        # Add shapes to batch
        circle = pyglet.shapes.Circle(x, y, radius, color, batch=batch)

    def update(self, dt):
        # Update state each frame
        pass

    def on_trigger(self, trigger_data):
        # Optional: handle TRIGGER messages
        pass

    def is_active(self):
        return self.enabled
```

3. **Add config section to config.toml:**

```toml
[visual_stimuli.my_stimulus]
enabled = true
param1 = value1
```

4. **Register in VisualStimuliProcess:**

In `src/processes/visual_stimuli.py`, add to `_initialize_stimuli()`:

```python
my_config = self.config.get("my_stimulus", {})
if my_config.get("enabled", False):
    my_stimulus = MyStimulus(my_config)
    self.registry.register("my_stimulus", my_stimulus)
```

## Pyglet Shapes Reference

```python
# Circle
pyglet.shapes.Circle(x, y, radius, color=(r,g,b), batch=batch)

# Rectangle
pyglet.shapes.Rectangle(x, y, width, height, color=(r,g,b), batch=batch)

# Line
pyglet.shapes.Line(x1, y1, x2, y2, width, color=(r,g,b), batch=batch)
```

## Coordinate Systems

- **Braid space**: Heading in radians, `arctan2(yvel, xvel)`
- **Display space**: Pixels (0-7680 width, 0-1080 height)
- **Stimulus positions**: Degrees relative to fly heading (e.g., [-90, 0, 90])

Use `GeometryUtils` to convert between systems:
- `heading_to_pixel_x(braid_heading_rad, offset_deg)` → pixel x-coordinate
- `degrees_to_pixels(angular_size_deg)` → radius in pixels

## Performance Notes

- Target: 240 Hz (4.17ms per frame)
- Use pyglet batched rendering (add all shapes to single batch)
- Pre-generate static content (don't create objects every frame)
- Monitor performance via logs (warnings if < 222 Hz)
