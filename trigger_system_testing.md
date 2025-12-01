# Two-Stage Trigger System Test Cases

## Configuration
From `config.toml`:
- Camera FOV: x=[-0.07, 0.07], y=[-0.045, 0.06] (14cm × 10.5cm area)
- Trigger radius: 0.05m (5cm from origin)
- Z-limits: [0.15, 0.25] (10cm vertical range)
- Heading cone: ±45° from direction to center
- Min trajectory time: 1.0s
- Min trigger interval: 10.0s (global cooldown)

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
- ✅ LENS message sent (if liquid_lens_active=true)
- ✅ TRIGGER message sent with trigger_type="recording"
- ❌ NO stimulation trigger (distance 0.072m > radius 0.05m)
- Result: Camera starts recording, lens starts tracking

### Test 4: Fly in Trigger Zone (Inner Zone), Heading to Center
**Position**: x=0.03, y=0.02, z=0.20 (distance from origin: ~0.036m)
**Heading**: Toward center
**Expected**:
- ✅ LENS message sent (if liquid_lens_active=true)
- ✅ TRIGGER message sent with trigger_type="recording"
- ✅ TRIGGER message sent with trigger_type="stimulation"
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
- ✅ LENS + recording trigger (in FOV)
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

## Message Flow Diagram

```
Fly enters FOV + heading to center:
├─> TriggerHandler checks:
│   ├─> Heading toward center? YES
│   ├─> Tracked ≥ 1.0s? YES
│   ├─> In camera FOV? YES
│   │   ├─> Cooldown expired? YES
│   │   │   ├─> Send LENS message
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
[TriggerHandler] Sent LENS trigger for object 1 at 1234567.890

[OptoTrigger] Ignoring trigger_type='recording' for object 1 (OptoTrigger only responds to 'stimulation')
[OptoTrigger] Received STIMULATION trigger for object 1 on frame 1234 (heading=1.57)

[Camera] (Rust binary handles both trigger types, starts recording)

[LiquidLens] (Receives TRIGGER, starts tracking)
```

## Implementation Summary

### Outer Zone (Camera FOV)
- **Condition**: Fly in camera FOV + heading toward center + cooldown expired
- **Actions**:
  - Liquid lens starts tracking (LENS message)
  - Camera starts recording (TRIGGER with type="recording")
  - Sets global cooldown timer

### Inner Zone (Trigger Radius)
- **Condition**: Also in cylindrical trigger zone (radius + z-limits)
- **Actions**:
  - Optogenetic LED activates (TRIGGER with type="stimulation")
  - Visual stimuli display (TRIGGER with type="stimulation")

### Backward Compatibility
- All TRIGGER messages include `trigger_type` field
- Defaults to "stimulation" if field missing (old code compatibility)
- Camera and lens ignore the field (work with all trigger types)
- OptoTrigger and VisualStimuli filter by trigger_type
