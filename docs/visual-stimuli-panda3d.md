# Visual Stimuli — Panda3D Pipeline

Developer guide for the Panda3D visual stimulus rendering system (`src/visual/`).

This documents the **Panda3D** pipeline. For the legacy pyglet pipeline, see [visual-stimuli.md](visual-stimuli.md).

## Overview

The Panda3D pipeline renders visual stimuli in a true 3D panoramic arena. Four perspective cameras split a 7680×1080 window into four 1920×1080 display regions, each covering a 90° quadrant around the fly. The fly is at origin; stimuli are placed on a virtual cylinder wall surrounding it.

**Key differences from the pyglet pipeline:**

| | pyglet (`src/stimuli/`) | Panda3D (`src/visual/`) |
|---|---|---|
| Rendering | 2D flat window | 3D scene graph with 4 cameras |
| Coordinates | Pixels via `GeometryUtils` | Angular degrees (heading, elevation, size) |
| Per-frame draw | `render(batch)` — shapes added to batch | Scene graph updates — Panda3D handles rendering |
| Visibility | `is_active()` returns bool | `stash()`/`unstash()` on scene-graph nodes |
| Setup | `initialize_rendering(batch)` | `setup()` — create geometry once |
| Timing | pyglet clock | Panda3D `ClockObject.getGlobalClock().getDt()` |

## Architecture

```
VisualProcess (ZMQ subscriber, process.py)
    |-- ArenaScene(ShowBase)              # 4-camera panoramic window
    |   |-- render                        # Root scene-graph node
    |   |   |-- BackgroundCylinder        # Textured cylinder (background.py)
    |   |   |-- LoomingDisk               # Billboard disk (looming.py)
    |   |   `-- OscillatingSquare         # Billboard square (tutorial)
    |   |-- camera[0..3]                  # Perspective cameras, 90° FOV each
    |   `-- taskMgr:
    |       |-- zmq_poll (sort=0)         # Non-blocking ZMQ receive
    |       `-- stimulus_update (sort=1)  # stim.update(dt) each frame
    |
    `-- _stimuli[]                        # List of BaseStimulus instances
        |-- BackgroundStimulus
        |-- LoomingStimulus
        `-- OscillatingSquare
```

**Initialization (once at startup):**
1. `VisualProcess._run()` loads config, creates `ArenaScene`
2. `_initialize_stimuli()` instantiates each enabled stimulus, calls `stim.setup()`, appends to `self._stimuli`
3. Two Panda3D task manager tasks are registered: `zmq_poll` and `stimulus_update`

**Per-frame loop (runs at display refresh rate):**
1. `zmq_poll_task`: non-blocking ZMQ receive → `stim.on_trigger(world_heading, data)` on ZONE_ENTER
2. `stimulus_update_task`: `stim.update(dt)` for each stimulus
3. Panda3D automatically renders the scene graph — no explicit draw call

**Key classes:**

| Class | File | Purpose |
|-------|------|---------|
| `BaseStimulus` | `src/visual/base.py` | ABC with `setup()`, `on_trigger()`, `update()`, angular helpers |
| `ArenaScene` | `src/visual/scene.py` | 4-camera `ShowBase` window, fly at origin |
| `VisualProcess` | `src/visual/process.py` | ZMQ subscriber, heading conversion, render loop |
| `BackgroundStimulus` | `src/visual/stimuli/background.py` | Always-visible textured cylinder |
| `LoomingStimulus` | `src/visual/stimuli/looming.py` | Expanding disk with L/V ratio dynamics |

## Core Concepts

### The Scene Graph

Panda3D manages a tree of `NodePath` objects rooted at `self.scene.render`. You attach geometry to this tree, and Panda3D walks it every frame to render all visible nodes. There is no per-frame batch — adding or removing nodes changes what appears on screen.

```python
# Attach a node to the scene
node = self.scene.render.attachNewNode(geom_node)
# Hide it without removing
node.stash()
# Show it again
node.unstash()
# Remove permanently
node.removeNode()
```

Use `stash()`/`unstash()` for temporary visibility (e.g., stimulus on/off). Use `removeNode()` when a node will never be used again.

### Angular Coordinate System

The fly sits at the origin. World: North = +Y, East = +X, Z = up. Heading is a compass bearing (0° = North, 90° = East, 180° = South, 270° = West).

Module-level helpers in `src/visual/base.py`:

```python
from src.visual.base import angular_to_world_pos, angular_size_to_radius

# Convert (heading, elevation, distance) to world (x, y, z) in cm
x, y, z = angular_to_world_pos(heading_deg=45.0, elevation_deg=0.0, distance_cm=25.0)

# Convert angular size to physical radius at a given distance
radius = angular_size_to_radius(size_deg=10.0, distance_cm=25.0)
```

`angular_size_to_radius` uses the formula `R = tan(angle / 2) * distance`. For a unit disk (radius=1), `node.setScale(R)` makes it subtend exactly `size_deg`. For a unit square (half-side=1), `node.setScale(R)` makes the half-side subtend `size_deg/2`.

### Billboards

A billboard node always faces the fly (at origin), even when placed off-axis:

```python
node.setBillboardPointWorld()   # Face the origin
node.setTwoSided(True)          # Visible from both sides
```

The `add_disk` helper in `BaseStimulus` applies both automatically. For custom geometry, you must set them yourself.

### State Machines

Closed-loop stimuli use a simple state machine:

```
IDLE --[on_trigger()]--> ACTIVE --[elapsed > duration]--> IDLE
```

`update(dt)` advances elapsed time and applies state transitions. The `LoomingStimulus` uses a three-state variant (IDLE, EXPANDING, HOLDING).

### Custom Geometry Pattern

When the built-in `add_disk` helper isn't the right shape, create a module-level factory function following the `_make_unit_disk` convention from `src/visual/base.py`:

```python
def _make_unit_square(color: tuple):
    """Create a flat square (half-side=1) in the XZ plane as a GeomNode."""
    from panda3d.core import (
        Geom, GeomNode, GeomTriangles,
        GeomVertexData, GeomVertexFormat, GeomVertexWriter,
    )
    # Convert 0-255 colors to 0-1 floats
    r, g, b = color[0] / 255.0, color[1] / 255.0, color[2] / 255.0
    # ... build vertices and triangles ...
    return node
```

Key points:
- Create geometry in the **XZ plane** (same as `_make_unit_disk`) so billboard rotation works correctly
- Scale the returned node with `setScale(angular_size_to_radius(...))`
- Defer `panda3d.core` imports inside the function body to avoid import errors in non-Panda3D environments

### No-Display Testing

Pure math functions (config parsing, state logic, coordinate helpers) can be tested without a display. Only tests that create an `ArenaScene` need `@pytest.mark.display`:

```python
# No display needed — pass scene=None, test __init__ and state logic
stim = OscillatingSquare({}, scene=None)
assert stim._size_deg == 10.0

# Display needed
@pytest.mark.display
def test_integration():
    scene = ArenaScene(standalone=True)
    stim = OscillatingSquare(config, scene)
    stim.setup()
    # ...
    scene.cleanup()
```

## Included Stimuli

### BackgroundStimulus (`src/visual/stimuli/background.py`)

Open-loop. Textured cylinder at `viewing_distance_cm` radius with a procedural random-square pattern, plus a ground plane. Always visible. No trigger/update logic — demonstrates the simplest possible `setup()`-only stimulus. Uses `_build_cylinder_geom` factory and loads textures via the Panda3D API directly.

```toml
[visual_stimuli.background]
enabled = true
square_size_px = 40
density = 0.5
background_color = [255, 255, 255]
foreground_color = [0, 0, 0]
seed = 42
cylinder_height_cm = 80
```

### LoomingStimulus (`src/visual/stimuli/looming.py`)

Closed-loop. Expanding billboard disk simulating an approaching object. Demonstrates: three-state machine (IDLE → EXPANDING → HOLDING), `add_disk` with explicit `distance_cm`, `set_angular_size` each frame, `remove_node` on completion, `PositionBalancer` for balanced position offsets, sham trials. The primary reference for closed-loop stimuli.

```toml
[visual_stimuli.looming]
enabled = true
initial_size_deg = 5.0
final_size_deg = 72.0
expansion_duration_ms = 300
hold_time_ms = 200
expansion_type = "exponential"     # "lv_ratio", "exponential", or "linear"
color = [0, 0, 0]
positions_deg = [-90, -45, 0, 45, 90]
sham_probability = 0.0
seed = 42
```

## Tutorial: Creating an OscillatingSquare

We'll build a closed-loop stimulus step by step: a small square billboard that appears in front of the fly when a ZONE_ENTER fires, then oscillates left and right for a fixed duration and disappears.

The complete implementation lives at `src/visual/stimuli/oscillating_square.py`. This tutorial shows the key parts inline; see that file for the full source.

### Step 1: Create the stimulus class

Create `src/visual/stimuli/oscillating_square.py`.

**Custom geometry factory.** Since `add_disk` creates circles, we need a square. Write a module-level factory following the `_make_unit_disk` pattern from `src/visual/base.py`:

```python
import math
import random

from src.visual.base import BaseStimulus, angular_to_world_pos, angular_size_to_radius


def _make_unit_square(color: tuple):
    """Create a flat square (half-side=1) in the XZ plane as a GeomNode."""
    from panda3d.core import (
        Geom, GeomNode, GeomTriangles,
        GeomVertexData, GeomVertexFormat, GeomVertexWriter,
    )

    r, g, b = color[0] / 255.0, color[1] / 255.0, color[2] / 255.0
    a = color[3] / 255.0 if len(color) == 4 else 1.0

    # Declare the per-vertex attribute layout: 3D position (V3) + RGBA color (C4).
    # getV3c4() is a built-in Panda3D format; custom layouts are possible but rarely needed.
    vformat = GeomVertexFormat.getV3c4()

    # Allocate the vertex buffer. "square" is just a debug name; UHStatic tells the
    # GPU this data won't change after upload (enables optimization).
    vdata = GeomVertexData("square", vformat, Geom.UHStatic)
    vdata.setNumRows(4)  # pre-allocate exactly 4 rows (one per corner)

    # GeomVertexWriter is a cursor into the vertex buffer for a specific column.
    # Each addData* call writes one value and advances the cursor by one row.
    vertex = GeomVertexWriter(vdata, "vertex")
    color_w = GeomVertexWriter(vdata, "color")

    # Write the four corners in XZ plane (Y=0), half-side = 1.
    # Each vertex.addData3 + color_w.addData4 pair writes one complete vertex.
    # The two writers advance in lockstep: row 0, row 1, row 2, row 3.
    vertex.addData3(1, 0, 1)    # corner 0: +X, +Z (top-right)
    color_w.addData4(r, g, b, a)
    vertex.addData3(1, 0, -1)   # corner 1: +X, -Z (bottom-right)
    color_w.addData4(r, g, b, a)
    vertex.addData3(-1, 0, -1)  # corner 2: -X, -Z (bottom-left)
    color_w.addData4(r, g, b, a)
    vertex.addData3(-1, 0, 1)   # corner 3: -X, +Z (top-left)
    color_w.addData4(r, g, b, a)

    # A rectangle needs two triangles. addVertices(i, j, k) references corners by
    # their row index in the vertex buffer (counter-clockwise winding = front face).
    # Triangle 0-1-2 covers the bottom-right half; 0-2-3 covers the top-left half.
    tris = GeomTriangles(Geom.UHStatic)
    tris.addVertices(0, 1, 2)
    tris.addVertices(0, 2, 3)
    tris.closePrimitive()  # signals that the index list is complete

    # Geom bundles a vertex buffer with one or more index primitives (our triangles).
    geom = Geom(vdata)
    geom.addPrimitive(tris)

    # GeomNode is a scene-graph node that holds renderable Geom objects.
    # addGeom attaches our geometry; the node can then be attached to the scene with
    # render.attachNewNode(node) or NodePath(node).
    node = GeomNode("square")
    node.addGeom(geom)
    return node
```

The square has half-side = 1 in the XZ plane. Scaling it by `angular_size_to_radius(size_deg, dist)` makes the half-side subtend `size_deg / 2` from the fly, so the full square subtends `size_deg`.

**Stimulus class skeleton:**

```python
class OscillatingSquare(BaseStimulus):

    IDLE = 0
    ACTIVE = 1

    def __init__(self, config: dict, scene):
        super().__init__(config, scene)
        self._size_deg: float = config.get("size_deg", 10.0)
        self._amplitude_deg: float = config.get("amplitude_deg", 30.0)
        self._frequency_hz: float = config.get("frequency_hz", 1.0)
        self._duration_ms: float = config.get("duration_ms", 2000.0)
        self._color: tuple = tuple(config.get("color", [0, 0, 0]))
        self._positions_deg: list = config.get("positions_deg", [-45, 0, 45])
        self._seed: int = config.get("seed", 42)
        self._rng = random.Random(self._seed)
        self._state = self.IDLE
        self._square = None
        self._elapsed_ms: float = 0.0
        self._base_heading: float = 0.0
        self._offset_deg: float = 0.0
```

**Implement `setup()`.** Create the square geometry once, stash it. It will be repositioned and unstashed on each trigger:

```python
def setup(self) -> None:
    square_geom = _make_unit_square(self._color)
    self._square = self.scene.render.attachNewNode(square_geom)
    self._square.setBillboardPointWorld()
    self._square.setTwoSided(True)
    self._square.stash()
```

Creating geometry in `setup()` and toggling with `stash()`/`unstash()` is more efficient than creating/destroying nodes on each trigger.

**Implement `on_trigger()`.** Pick a random offset from `positions_deg`, compute the world heading, and place the square:

```python
def on_trigger(self, heading_deg: float, trigger_data: dict) -> None:
    if self._state != self.IDLE:
        return
    self._state = self.ACTIVE
    self._base_heading = heading_deg
    self._offset_deg = self._rng.choice(self._positions_deg)
    self._elapsed_ms = 0.0
    self._place_square(heading_deg + self._offset_deg)
```

**Helper: `_place_square()`.** Converts heading to world position, scales the square to the correct angular size, un-stashes it:

```python
def _place_square(self, heading_deg: float) -> None:
    dist = self.scene.viewing_distance_cm - 1.0
    x, y, z = angular_to_world_pos(heading_deg, 0.0, dist)
    radius = angular_size_to_radius(self._size_deg, dist)
    self._square.setPos(x, y, z)
    self._square.setScale(radius)
    self._square.unstash()
```

The distance is 1 cm inside the cylinder wall so the square renders in front of the background.

**Implement `update()`.** Advance elapsed time; if duration exceeded, stash and return to IDLE. Otherwise oscillate the heading with a sine wave:

```python
def update(self, dt: float) -> None:
    if self._state != self.ACTIVE:
        return

    self._elapsed_ms += dt * 1000.0

    if self._elapsed_ms >= self._duration_ms:
        self._square.stash()
        self._state = self.IDLE
        return

    t_sec = self._elapsed_ms / 1000.0
    oscillation = (
        math.sin(t_sec * self._frequency_hz * 2.0 * math.pi) * self._amplitude_deg
    )
    current_heading = self._base_heading + self._offset_deg + oscillation
    self._place_square(current_heading)
```

The square's position is updated every frame. Angular size stays constant — only heading changes.

### Step 2: Register in VisualProcess

In `src/visual/process.py`, add the import:

```python
from src.visual.stimuli.oscillating_square import OscillatingSquare
```

Then add a registration block to `_initialize_stimuli()`:

```python
if cfg.get("oscillating_square", {}).get("enabled", False):
    stim = OscillatingSquare(
        cfg.get("oscillating_square", {}), self._scene
    )
    stim.setup()
    self._stimuli.append(stim)
    self.logger.info("Registered: OscillatingSquare")
```

The `cfg.get("oscillating_square", {})` call reads the `[visual_stimuli.oscillating_square]` config subsection. The pattern is identical to `BackgroundStimulus` and `LoomingStimulus`.

### Step 3: Add config

In `configs/visual_stimuli.toml` (or the example file):

```toml
[visual_stimuli.oscillating_square]
enabled = false

# Square appearance
size_deg = 10.0
color = [0, 0, 0]

# Oscillation parameters
amplitude_deg = 30.0          # Peak left/right displacement
frequency_hz = 2.0            # Oscillation cycles per second

# Timing
duration_ms = 2000            # Total presentation time

# Position offsets from fly heading (randomly chosen)
# 0 = ahead, 90 = right, -90 = left, 180 = behind
positions_deg = [-45, 0, 45]

seed = 42
```

### Step 4: Run it

Standalone test mode (small 1280×320 window, no hardware):

```bash
uv run python -c "
from direct.task import Task
from panda3d.core import ClockObject
from src.visual.scene import ArenaScene
from src.visual.stimuli.oscillating_square import OscillatingSquare

scene = ArenaScene(standalone=True)
stim = OscillatingSquare({
    'size_deg': 10.0,
    'amplitude_deg': 30.0,
    'frequency_hz': 2.0,
    'duration_ms': 2000.0,
    'color': [0, 0, 0],
    'positions_deg': [0],
}, scene)
stim.setup()
stim.on_trigger(0.0, {'obj_id': 1})

# Panda3D's render loop does NOT call stim.update() automatically.
# Register a per-frame task so the oscillation advances each frame.
# (VisualProcess does this via _stimulus_update_task in the full rig.)
def update_task(task):
    stim.update(ClockObject.getGlobalClock().getDt())
    return Task.cont

scene.taskMgr.add(update_task, 'update_stim')
scene.run()  # blocks; close the window to exit
```

In the full rig, the stimulus triggers automatically on ZONE_ENTER from the Braid tracker. Run with the main process:

```bash
uv run python main.py
```

### Step 5: Write tests

Create `tests/visual/test_oscillating_square.py`.

**Unit tests (no display needed).** Test config parsing, state machine, and the geometry factory:

```python
from src.visual.stimuli.oscillating_square import OscillatingSquare, _make_unit_square

def test_default_config():
    stim = OscillatingSquare({}, scene=None)
    assert stim._size_deg == 10.0
    assert stim._amplitude_deg == 30.0
    assert stim._frequency_hz == 1.0
    assert stim._duration_ms == 2000.0

def test_custom_config():
    stim = OscillatingSquare(
        {"size_deg": 20.0, "color": [255, 128, 64], "positions_deg": [0, 90]},
        scene=None,
    )
    assert stim._size_deg == 20.0
    assert stim._color == (255, 128, 64)

def test_state_starts_idle():
    stim = OscillatingSquare({}, scene=None)
    assert stim._state == OscillatingSquare.IDLE

def test_double_trigger_ignored():
    stim = OscillatingSquare({"positions_deg": [0]}, scene=None)
    stim._state = OscillatingSquare.ACTIVE
    stim._base_heading = 90.0
    stim.on_trigger(0.0, {})
    assert stim._base_heading == 90.0  # unchanged

def test_make_unit_square_creates_geomnode():
    node = _make_unit_square((255, 0, 0))
    assert node.getName() == "square"
    assert node.getNumGeoms() == 1
```

**Integration test (needs display).** Test the full lifecycle with a real `ArenaScene`:

```python
import pytest

@pytest.mark.display
def test_setup_and_trigger_cycle():
    from src.visual.scene import ArenaScene

    scene = ArenaScene(standalone=True)
    stim = OscillatingSquare(
        {"size_deg": 10.0, "amplitude_deg": 30.0, "frequency_hz": 2.0,
         "duration_ms": 2000.0, "color": [0, 0, 0], "positions_deg": [0]},
        scene,
    )
    stim.setup()
    assert stim._square.isStashed()

    stim.on_trigger(0.0, {"obj_id": 1})
    assert stim._state == OscillatingSquare.ACTIVE
    assert not stim._square.isStashed()

    # Run 2000ms of updates
    for _ in range(80):
        stim.update(0.025)
    assert stim._state == OscillatingSquare.IDLE
    assert stim._square.isStashed()

    scene.cleanup()
```

Run tests:

```bash
uv run pytest tests/visual/test_oscillating_square.py -v          # unit tests
uv run pytest tests/visual/test_oscillating_square.py -v -m display  # integration test
```

## Advanced Topics

### Position Balancing

For balanced presentation across positions, use the `PositionBalancer` class from `src/visual/stimuli/looming.py`:

```python
from src.visual.stimuli.looming import PositionBalancer

class MyStimulus(BaseStimulus):
    def setup(self) -> None:
        self._balancer = PositionBalancer(
            self.config.get("positions_deg", [-45, 0, 45])
        )

    def on_trigger(self, heading_deg, trigger_data):
        self._offset_deg = self._balancer.next()
```

It shuffles the position list each cycle, so every position is used once before any repeats — unlike `random.choice` which can repeat positions by chance.

### Duration Precision

`update(dt)` accumulates elapsed time using the Panda3D frame delta. The stimulus lasts until the first frame after `duration_ms` has been exceeded, so actual duration may be up to one frame longer. For exact timing requirements, use a HOLDING phase (like `LoomingStimulus`) to guarantee a minimum hold after expansion.

### Performance

- Billboards are one draw call each — efficient even at scale
- Prefer `stash()`/`unstash()` over `removeNode()`/`attachNewNode()` for repeated show/hide cycles
- Keep `update(dt)` math simple — no per-frame allocations, no `sqrt` unless needed
- Pre-calculate constants in `setup()` or `__init__()`, not in `update()`

## BaseStimulus Interface Reference

```python
class BaseStimulus(ABC):
    def __init__(self, config: dict, scene: "ArenaScene"):
        """config is the subsection for this stimulus (e.g., cfg.get("looming", {})).
           scene is the ArenaScene instance (access .render, .viewing_distance_cm, etc.)"""

    @abstractmethod
    def setup(self) -> None:
        """Called once at startup. Create scene nodes using attachNewNode().
        Stash nodes that start invisible."""

    @abstractmethod
    def on_trigger(self, heading_deg: float, trigger_data: dict) -> None:
        """Called on ZONE_ENTER. heading_deg is Braid→world converted (0=North, 90=East).
        trigger_data has: obj_id, frame, timestamp, x, y, z, xvel, yvel, zvel, mean_heading (Braid radians)."""

    @abstractmethod
    def update(self, dt: float) -> None:
        """Called every frame. dt is seconds since last frame. Animate geometry here."""

    # Angular API helpers (all use self.scene.render internally)

    def add_disk(self, heading_deg: float, size_deg: float,
                 elevation_deg: float = 0.0, color: tuple = (0, 0, 0),
                 distance_cm: float | None = None) -> "NodePath":
        """Place a billboard disk. Returns the NodePath."""

    def set_angular_size(self, node: "NodePath", size_deg: float) -> None:
        """Update an existing billboard's angular size in-place."""

    def remove_node(self, node: "NodePath") -> None:
        """Remove a node from the scene graph."""

# Module-level helpers in src/visual/base.py

def angular_to_world_pos(heading_deg, elevation_deg, distance_cm) -> tuple[float, float, float]:
    """Convert angular position to world (x, y, z) in centimeters."""

def angular_size_to_radius(size_deg, distance_cm) -> float:
    """Convert angular diameter to physical radius in centimeters."""

def _make_unit_disk(color, num_segments=32) -> GeomNode:
    """Create a flat disk (radius=1) in the XZ plane. Returns a GeomNode."""
```

## Troubleshooting

**Stimulus not appearing:**
- Check `enabled = true` in the config section (`[visual_stimuli.my_stimulus]`)
- Verify the registration log: `"Registered: MyStimulus"` appears in startup logs
- Ensure `setup()` creates geometry and the node is attached to `self.scene.render`
- Check that `unstash()` is called in `on_trigger()` (stashed nodes are invisible)
- For large angular sizes, the square/disk may be behind the cylinder wall — pass `distance_cm` explicitly (see LoomingStimulus for the formula)

**Wrong position:**
- North = +Y, East = +X, Z = up
- Camera heading is negated internally (`setH(-heading_deg)`) because Panda3D H is counter-clockwise while compass bearings are clockwise
- The offset is added to the fly's heading: `base_heading + offset_deg + oscillation`

**No oscillation:**
- Check `amplitude_deg` is nonzero
- Verify `frequency_hz` produces visible motion (1–5 Hz recommended at 60fps)
- The sine argument is `t * frequency * 2π`, not `t * frequency`

**Square flickers or disappears at edges:**
- `setBillboardPointWorld()` should keep it facing the fly
- If the square crosses a camera boundary, check that it sits at the correct distance from origin
- `setTwoSided(True)` ensures both faces render
