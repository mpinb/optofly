# OptoFly Trigger Handler Fixes Summary

**Branch**: `fix-trigger-handler-issues`
**Date**: 2025-12-01
**Base Commit**: 5443b97

---

## Issues Fixed

### Issue #1: Camera FOV Structure Mismatch ✅ FIXED
**Severity**: HIGH - Would cause runtime crash
**Commit**: 8e297ba

**Problem**: Config.toml uses dict structure for FOV, but code expected nested list.

**Fix**:
- Changed `TriggerHandler.__init__()` to load FOV as individual attributes
- Stores `fov_x_min`, `fov_x_max`, `fov_y_min`, `fov_y_max`
- Simplified `is_in_camera_fov()` to use direct attribute access
- Removed unnecessary try/except fallback code

**Files Modified**:
- `src/processes/tracking.py` (lines 199-205, 275-288)

---

### Issue #2: Z-Limit Validation Gap ✅ FIXED
**Severity**: MEDIUM - Invalid config could pass validation
**Commit**: 10f5117

**Problem**: No validation that z_lim[0] < z_lim[1], reversed limits would prevent all triggers.

**Fix**:
- Added validation in `TriggerHandlerConfig.__init__()`
- Checks z_lim[0] < z_lim[1] (prevents reversed limits)
- Validates reasonable bounds (-1.0m to 2.0m)
- Raises clear ValueError with diagnostic message

**Files Modified**:
- `src/utils/config.py` (lines 76-88)

---

### Issue #3: Global Trigger Rate Limiting ℹ️ DESIGN DECISION
**Severity**: N/A - Intentional behavior
**Status**: No changes needed

**Clarification**: The 10-second global cooldown across all objects is intentional. This protects hardware and ensures well-separated stimulations.

---

## New Feature: Two-Stage Trigger System ✨

**Commits**: 7db1161, 306172f, 45af49d, d3ad8cf

### Overview
Implemented sophisticated two-stage trigger logic with separate zones for recording and stimulation.

### Architecture

**Outer Zone (Adaptive)**:
The outer zone adapts based on camera availability:

*Camera Active* (`camera.active = true`):
- Uses camera FOV boundaries (rectangular: 14cm × 10.5cm)
- Trigger condition: Fly in camera FOV + heading toward center

*Camera Inactive* (`camera.active = false`):
- Uses expanded cylindrical radius (radius + radius_expansion = 0.08m)
- Trigger condition: Fly within expanded radius + heading toward center

*Actions (both modes)*:
  - Liquid lens starts tracking (LENS message, if active)
  - Camera starts recording (TRIGGER with type="recording", if active)
  - Global cooldown timer set

**Inner Zone (Trigger Radius)**:
- Fixed cylindrical zone (radius = 0.05m, z-limits = [0.15, 0.25])
- Trigger condition: Also in cylindrical trigger zone
- Actions:
  - Optogenetic LED activates (TRIGGER with type="stimulation")
  - Visual stimuli display (TRIGGER with type="stimulation")

### Implementation Details

**1. TriggerHandler Core Logic** (`src/processes/tracking.py`)
- Modified `_evaluate_triggers()` with nested zone checks
- Updated `_send_trigger()` to accept `trigger_type` parameter
- Added `trigger_type` field to all TRIGGER messages
- Both zones respect global 10-second cooldown

**2. OptoTrigger Filtering** (`src/processes/led.py`)
- Added trigger_type check in `_handle_trigger()`
- Only activates LED when `trigger_type == "stimulation"`
- Logs debug message for ignored "recording" triggers
- Added `trigger_type` to CSV output

**3. Visual Stimuli Filtering** (`src/stimuli/registry.py`)
- Added trigger_type check in `StimulusRegistry.on_trigger()`
- Only dispatches to stimuli when `trigger_type == "stimulation"`
- Early return for "recording" triggers

**4. Camera & Lens Compatibility** (No changes needed)
- Camera: Python wrapper manages Rust subprocess, doesn't parse messages
- Lens: Receives TRIGGER messages, ignores unknown fields
- Both work correctly with new `trigger_type` field

### Backward Compatibility
- All TRIGGER messages include `trigger_type` field
- Defaults to "stimulation" if field missing (old code compatibility)
- OptoTrigger and VisualStimuli use default for missing field
- Camera and Lens ignore the field entirely

---

## New Feature: Adaptive Outer Zone ✨

**Commits**: 12f5cc6, 8c3f867, bb42781

### Overview
Added adaptive outer zone selection that adjusts based on camera availability, enabling camera-less operation with expanded cylindrical trigger zone.

### Configuration
Added new parameter: `radius_expansion` (default: 0.03m)
- When camera active: Outer zone uses camera FOV (rectangular)
- When camera inactive: Outer zone uses radius + radius_expansion (cylindrical)

### Implementation

**1. Configuration Updates** (`src/utils/config.py`, `config.toml`)
- Added `radius_expansion` parameter to `TriggerHandlerConfig`
- Added `camera_active` flag to check camera status
- Default expansion: 0.03m (total outer zone: 0.08m when camera off)

**2. Trigger Logic Updates** (`src/processes/tracking.py`)
- Added `is_in_expanded_radius_zone()` method
- Modified `_evaluate_triggers()` to check `camera_active` flag
- Dynamically selects FOV or expanded radius for outer zone check
- Inner zone (trigger radius) remains unchanged

**3. Documentation Updates**
- Updated test cases with adaptive outer zone scenarios
- Added Test 9: Expanded Radius Mode (camera inactive)
- Documented both operating modes in implementation summary

### Use Cases
- **Laboratory setup**: Camera + FOV-based outer zone (precise rectangular area)
- **Field setup**: No camera + radius-based outer zone (simpler cylindrical area)
- **Development/testing**: Test trigger logic without camera hardware

---

## Testing Documentation

See `trigger_system_testing.md` for:
- 9 comprehensive test cases (including adaptive outer zone)
- Message flow diagrams
- Expected log output
- Configuration reference
- Adaptive outer zone test scenarios

---

## Summary of Changes

### Files Modified
1. `src/processes/tracking.py` - Two-stage trigger logic + adaptive outer zone
2. `src/processes/led.py` - OptoTrigger filtering by trigger_type
3. `src/stimuli/registry.py` - Visual stimuli filtering by trigger_type
4. `src/utils/config.py` - Z-limit validation + radius_expansion config
5. `config.toml` - Added radius_expansion parameter

### Files Verified (No Changes Needed)
1. `src/processes/camera.py` - Works with new message format
2. `src/processes/lens.py` - Works with new message format

### New Documentation
1. `trigger_system_testing.md` - Test cases, flow diagrams, adaptive outer zone tests
2. `FIXES_SUMMARY.md` - This file (comprehensive implementation summary)

---

## Git Commits

```
# Critical Fixes
8e297ba - fix: correct camera FOV loading to match config.toml structure
10f5117 - fix: add z_lim validation to prevent invalid trigger zone configuration

# Two-Stage Trigger System
7db1161 - feat: implement two-stage trigger system in TriggerHandler
306172f - feat: add trigger_type filtering to OptoTrigger
45af49d - feat: add trigger_type filtering to Visual Stimuli
d3ad8cf - docs: add two-stage trigger system test cases and documentation
b38ce33 - docs: add comprehensive fixes and implementation summary

# Adaptive Outer Zone (Camera-less Operation)
12f5cc6 - feat: add radius_expansion parameter for camera-less operation
8c3f867 - feat: implement adaptive outer zone selection based on camera status
bb42781 - docs: update test cases for adaptive outer zone feature

# Code Cleanup
9d4d965 - refactor: remove unused LENS topic and _send_lens_trigger() method
9bb1353 - docs: update test documentation to remove LENS topic references
```

---

## Code Cleanup: Removed Dead LENS Topic ✨

**Commits**: 9d4d965, 9bb1353

### Issue Discovered
The `_send_lens_trigger()` method was sending messages to the LENS topic, but the LiquidLens process doesn't subscribe to that topic - it only subscribes to BRAID and TRIGGER topics.

### Root Cause
The lens receives TRIGGER messages (both "recording" and "stimulation" types) and uses those to start tracking. The separate LENS messages were being sent but never received - pure dead code.

### Fix Applied
- **Removed** `_send_lens_trigger()` method (was ~17 lines)
- **Removed** calls to `_send_lens_trigger()` in `_evaluate_triggers()`
- **Updated** documentation to reflect actual message flow

### Verification
Confirmed liquid lens receives TRIGGER messages at `lens.py:472-490` and starts tracking correctly. No functionality lost - this was pure dead code removal.

---

## Next Steps

1. **Merge to main**: Review changes and merge branch
2. **Test with hardware**: Run full system test with real tracking data
3. **Monitor logs**: Verify trigger_type filtering works as expected
4. **Update user docs**: Add two-stage trigger system to user manual

---

## Configuration Reference

### Configuration Values (from config.toml)
- Camera FOV: x=[-0.07, 0.07], y=[-0.045, 0.06] (14cm × 10.5cm)
- Trigger radius: 0.05m (5cm from origin)
- Z-limits: [0.15, 0.25] (10cm vertical range)
- Heading cone: ±45° from direction to center
- Min trajectory time: 1.0s
- Min trigger interval: 10.0s (global cooldown)
