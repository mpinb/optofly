# Two-Stage Trigger System Test Cases

## Configuration
From `config.toml`:
- Camera FOV: x=[-0.07, 0.07], y=[-0.045, 0.06] (14cm × 10.5cm area)
- Trigger radius: 0.05m (5cm from origin - inner zone)
- Radius expansion: 0.03m (added to radius when camera inactive)
- Z-limits: [0.15, 0.25] (10cm vertical range)
- Heading cone: ±45° from direction to center
- Min trajectory time: 1.0s
- Min trigger interval: 10.0s (global cooldown)

### Adaptive Outer Zone
The system adapts outer zone based on camera availability:
- **Camera active** (`camera.active = true`): Outer zone uses camera FOV boundaries (rectangular)
- **Camera inactive** (`camera.active = false`): Outer zone uses expanded radius = 0.08m (cylindrical)

## Test Cases

### Test 1: Fly Outside FOV
**Position**: x=0.10, y=0.0, z=0.20
**Heading**: Toward center (0°)
**Expected**:
- ❌ No triggers (outside FOV)
- Reason: x=0.10 > fov_x_max=0.07

### Test 2: Fly in FOV, Not Heading to Center
**Position**: x=0.02, y=0.02, z=0.20
**Heading**: Away from center (180° from angle-to-center)
**Expected**:
- ❌ No triggers (not heading toward center)
- Reason: Heading check fails

### Test 3: Fly in FOV (Outer Zone), Heading to Center
**Position**: x=0.06, y=0.04, z=0.20 (distance from origin: ~0.072m)
**Heading**: Toward center
**Expected**:
- ✅ TRIGGER message sent with trigger_type="recording"
  - Liquid lens starts tracking (if active)
  - Camera starts recording (if active)
- ❌ NO stimulation trigger (distance 0.072m > radius 0.05m)
- Result: Camera records, lens tracks

### Test 4: Fly in Trigger Zone (Inner Zone), Heading to Center
**Position**: x=0.03, y=0.02, z=0.20 (distance from origin: ~0.036m)
**Heading**: Toward center
**Expected**:
- ✅ TRIGGER message sent with trigger_type="recording"
  - Liquid lens starts tracking (if active)
  - Camera starts recording (if active)
- ✅ TRIGGER message sent with trigger_type="stimulation"
  - LED activates, visual stimuli display
- Result: Camera records, lens tracks, LED activates, visual stimuli display

### Test 5: Fly at Origin
**Position**: x=0.0, y=0.0, z=0.20
**Heading**: Stationary (no heading)
**Expected**:
- ❌ No triggers (no headings in history if velocity < min_velocity)
- Reason: is_heading_toward_center returns False (no heading data)

### Test 6: Fly Below Z-limit
**Position**: x=0.02, y=0.02, z=0.10
**Heading**: Toward center
**Expected**:
- ✅ TRIGGER message sent with trigger_type="recording" (in FOV)
  - Liquid lens starts tracking (if active)
  - Camera starts recording (if active)
- ❌ NO stimulation trigger (z=0.10 < z_lim[0]=0.15)

### Test 7: Cooldown Test
**Position**: Fly enters FOV at t=0s, exits, re-enters at t=5s
**Expected**:
- t=0s: ✅ Triggers sent
- t=5s: ❌ No triggers (5s < min_trigger_interval=10s)
- t=11s: ✅ Triggers sent (cooldown expired)

### Test 8: Minimum Trajectory Time
**Position**: Fly tracked for 0.5s, in FOV, heading to center
**Expected**:
- ❌ No triggers (0.5s < min_trajectory_time=1.0s)
- After 1.0s of tracking: ✅ Triggers sent

### Test 9: Expanded Radius Mode (Camera Inactive)
**Config**: `camera.active = false`
**Position A**: x=0.06, y=0.04, z=0.20 (distance: 0.072m)
**Position B**: x=0.03, y=0.02, z=0.20 (distance: 0.036m)
**Heading**: Toward center

**Expected for Position A** (in expanded zone, outside inner zone):
- ✅ TRIGGER message sent with trigger_type="recording"
  - Liquid lens starts tracking (if active)
  - Camera starts recording (if active)
- ❌ NO stimulation trigger (0.072m > radius=0.05m but < expanded_radius=0.08m)
- Note: Camera won't record (inactive), but trigger message still sent

**Expected for Position B** (in both zones):
- ✅ TRIGGER message sent with trigger_type="recording"
  - Liquid lens starts tracking (if active)
  - Camera starts recording (if active)
- ✅ TRIGGER message sent with trigger_type="stimulation"
  - LED activates, visual stimuli display
- Result: Lens tracks, LED activates, visual stimuli display

**Key difference from FOV mode**:
- Outer zone is cylindrical (0.08m radius) instead of rectangular (camera FOV)
- Same z-limits apply [0.15, 0.25]

## Message Flow Diagram

```
Fly enters FOV + heading to center:
├─> TriggerHandler checks:
│   ├─> Heading toward center? YES
│   ├─> Tracked ≥ 1.0s? YES
│   ├─> In camera FOV? YES
│   │   ├─> Cooldown expired? YES
│   │   │   ├─> Send TRIGGER (type="recording")
│   │   │   ├─> Update last_trigger_time
│   │   │   └─> Check if in trigger zone?
│   │   │       ├─> YES: Send TRIGGER (type="stimulation")
│   │   │       └─> NO: Done

Downstream processes:
├─> LiquidLens: Receives TRIGGER (both types), starts tracking
├─> Camera: Receives TRIGGER (both types), starts recording
├─> OptoTrigger: Receives TRIGGER, activates LED only if type="stimulation"
└─> VisualStimuli: Receives TRIGGER, displays only if type="stimulation"
```

## Expected Log Output

```
[TriggerHandler] Sent TRIGGER (recording) for object 1 (frame=1234, heading=1.57)
[TriggerHandler] Sent TRIGGER (stimulation) for object 1 (frame=1234, heading=1.57)

[OptoTrigger] Ignoring trigger_type='recording' for object 1 (OptoTrigger only responds to 'stimulation')
[OptoTrigger] Received STIMULATION trigger for object 1 on frame 1234 (heading=1.57)

[Camera] (Rust binary handles both trigger types, starts recording)

[LiquidLens] Received trigger for object 1 on frame 1234
[LiquidLens] (Starts tracking, subscribes to BRAID for position updates)
```

## Implementation Summary

### Outer Zone (Adaptive)
The outer zone adapts based on camera availability:

**Camera Active** (`camera.active = true`):
- **Condition**: Fly in camera FOV (rectangular) + heading toward center + cooldown expired
- **Outer Zone**: Camera FOV boundaries (x: [-0.07, 0.07], y: [-0.045, 0.06])

**Camera Inactive** (`camera.active = false`):
- **Condition**: Fly in expanded radius (cylindrical) + heading toward center + cooldown expired
- **Outer Zone**: Expanded cylindrical zone (radius + radius_expansion = 0.08m)

**Actions (both modes)**:
  - Send TRIGGER message with type="recording"
    - Liquid lens starts tracking (if active)
    - Camera starts recording (if active)
  - Sets global cooldown timer

### Inner Zone (Trigger Radius)
- **Condition**: Also in cylindrical trigger zone (radius + z-limits)
- **Zone**: Cylindrical (radius = 0.05m, z: [0.15, 0.25])
- **Actions**:
  - Optogenetic LED activates (TRIGGER with type="stimulation")
  - Visual stimuli display (TRIGGER with type="stimulation")

### Backward Compatibility
- All TRIGGER messages include `trigger_type` field
- Defaults to "stimulation" if field missing (old code compatibility)
- Camera and lens ignore the field (work with all trigger types)
- OptoTrigger and VisualStimuli filter by trigger_type
