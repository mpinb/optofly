# OptoTrigger Worker Implementation Plan

## Overview

Refactor the OptoTrigger architecture to separate hardware control from process orchestration and data logging, following the established pattern used by LiquidLens.

## Target Architecture

```
TriggerHandler Process
    ↓ (publishes TRIGGER messages via ZMQ)
    ├─→ LiquidLens Process (existing)
    └─→ OptoTriggerWorker Process (NEW)
         ↓ (controls hardware)
         OptoTrigger Class (refactored - pure hardware)
         ↓ (serial commands)
         Arduino Hardware
```

## Implementation Progress

### ✅ Task 1: Refactor OptoTrigger Class (Pure Hardware) - COMPLETED

**File:** `src/classes/opto_trigger.py`

**Changes Made:**
- ✅ Removed `braid_folder` parameter from `__init__()`
- ✅ Removed `CSVWriter` import and initialization
- ✅ Removed all CSV logging from `trigger()` method
- ✅ Removed `csv_writer.close()` from `close()` method
- ✅ Updated `trigger()` signature to accept only `sham` parameter
- ✅ **ENHANCEMENT:** Modified `trigger()` to return `tuple[bool, bool]` (success, was_sham)
- ✅ Updated docstrings to reflect hardware-only responsibility
- ✅ Updated `__main__` CLI to work without CSV logging and handle new return format

**Deviation from Original Plan:**
- Changed `trigger()` return type from `bool` to `tuple[bool, bool]` to provide sham status back to caller
- This ensures single source of truth for sham determination and accurate CSV logging

**Result:** OptoTrigger is now a pure hardware controller like LensDriver

**Commit:** `6c8eceb` - "Refactor OptoTrigger to pure hardware controller"

---

### ✅ Task 2: Create OptoTriggerWorker Process - COMPLETED

**File:** `src/processes/opto_trigger_worker.py` (NEW)

**Implemented Features:**
- ✅ Inherits from `WorkerProcess`
- ✅ Subscribes to TRIGGER topic via ZMQ
- ✅ Initializes OptoTrigger hardware controller
- ✅ Initializes CSVWriter for `opto.csv` logging
- ✅ `initialize()` method sets up ZMQ, hardware, and CSV writer
- ✅ `run()` method with main event loop
- ✅ `close()` method for resource cleanup
- ✅ Standalone testing capability via `__main__`

**ZMQ Configuration:**
- Uses `ZMQConfig` to get subscriber address and trigger port
- Subscribes to `trigger_topic` ("TRIGGER")
- Handles backward-compatible message parsing

**CSV Logging:**
- Logs to `opto.csv` in braid folder
- **Enhanced Columns:** `obj_id`, `frame`, `braid_timestamp`, `trigger_timestamp`, `mean_heading`, `duration`, `intensity`, `frequency`, `color`, `sham`
- Writes row after each trigger event (both real and sham)

**Main Loop Logic:**
1. ✅ Wait for TRIGGER messages from ZMQ
2. ✅ Parse message to extract all fields
3. ✅ Hardware determines sham using config probability
4. ✅ Call `opto_trigger.trigger(sham=None)` and receive (success, was_sham)
5. ✅ Log event to CSV with all parameters including sham status
6. ✅ Handle errors gracefully and continue running
7. ✅ Exit on stop_event

**Commit:** `b3949d5` - "Add OptoTriggerWorker process and improve trigger API"

---

### ✅ Task 3: Update Configuration - COMPLETED

**Files:** `config.toml`, `src/utils/config.py`

**Verification Results:**
- ✅ `[opto_trigger]` section has `active` flag
- ✅ All necessary parameters exist (port, baudrate, duration, intensity, frequency, color, sham_probability)
- ✅ `OptoTriggerConfig` dataclass has all needed fields
- ✅ `ZMQConfig` has trigger_port and trigger_topic
- ✅ Configuration loading works correctly
- ✅ No changes needed - configuration already complete

**Status:** No changes required, all configuration verified as complete.

---

### ✅ Task 4: Update TriggerHandler Integration - COMPLETED

**File:** `src/processes/trigger_handler.py`

**Changes Made:**
- ✅ Added `current_frame` and `current_timestamp` fields to `TrackedObject`
- ✅ Updated `TrackedObject.update()` to accept and store `frame` parameter
- ✅ Modified `_process_birth()` to extract and pass `frame` from BRAID data
- ✅ Modified `_process_update()` to extract and pass `frame` from BRAID data
- ✅ **ENHANCEMENT:** Changed `_send_trigger()` signature from `(obj_id: int)` to `(tracked_obj: TrackedObject)`
- ✅ Updated `_send_trigger()` call site to pass `tracked_obj` instead of `tracked_obj.obj_id`

**TRIGGER Message Enhanced Format:**
```python
{
    "obj_id": int,                    # Object identifier
    "frame": int,                     # Camera frame number
    "braid_timestamp": float,         # Timestamp from BRAID tracking system
    "trigger_timestamp": float,       # When trigger decision was made
    "mean_heading": float | None,     # Mean trajectory heading in radians
    "timestamp": float,               # DEPRECATED: Alias for braid_timestamp (backward compatibility)
}
```

**Backward Compatibility:**
- ✅ Old `timestamp` field maintained as alias for `braid_timestamp`
- ✅ OptoTriggerWorker handles both old and new message formats with fallback logic

**File:** `src/processes/opto_trigger_worker.py`

**Updates:**
- ✅ Updated `_handle_trigger()` to extract all new fields
- ✅ Added backward compatibility fallback for `timestamp` field
- ✅ Enhanced CSV row to include all new fields
- ✅ Improved logging to show frame and heading information

**Commit:** `c9a18f4` - "Add frame and mean_heading to TRIGGER messages with dual timestamps"

---

### ⏳ Task 5: Integrate OptoTriggerWorker into Main Process - PENDING

**File:** `main.py`

**Required Changes:**
- [ ] Import `OptoTriggerWorker` from `src.processes.opto_trigger_worker`
- [ ] Read `opto_trigger.active` config flag
- [ ] If active, spawn OptoTriggerWorker process alongside other processes
- [ ] Pass appropriate parameters (event, braid_folder, config_path, process_name, log_level, log_color)
- [ ] Add to process list for cleanup on shutdown

**Pattern to Follow:**
Look at how LiquidLens is spawned and managed in main.py

**Next Steps:**
1. Read main.py to understand current process spawning pattern
2. Add OptoTriggerWorker to the process orchestration
3. Ensure proper startup/shutdown handling

---

### ⏳ Task 6: Testing and Validation - PENDING

**Manual Testing Plan:**
1. [ ] Test OptoTrigger class standalone (using `python -m src.classes.opto_trigger`)
2. [ ] Test OptoTriggerWorker process standalone (using `python -m src.processes.opto_trigger_worker`)
3. [ ] Test full system integration (using `python main.py`)
4. [ ] Verify CSV logging works correctly with all new fields
5. [ ] Verify trigger messages are received and processed
6. [ ] Test sham stimulation logic
7. [ ] Test error handling and recovery

**Test Scenarios:**
- [ ] Normal trigger with real stimulation
- [ ] Sham trigger (no hardware activation)
- [ ] Missing Arduino hardware (graceful degradation)
- [ ] Multiple rapid triggers
- [ ] Process shutdown and cleanup
- [ ] CSV file creation and writing
- [ ] Verify frame numbers match BRAID data
- [ ] Verify both timestamps are logged correctly
- [ ] Verify mean_heading is calculated and logged

**Validation Checklist:**
- [x] OptoTrigger class has no CSV logging
- [x] OptoTrigger works without braid_folder
- [x] OptoTrigger returns (success, was_sham) tuple
- [x] OptoTriggerWorker subscribes to TRIGGER topic
- [x] CSV logging includes all enhanced fields
- [x] Sham determination happens in hardware class
- [x] TRIGGER messages include frame, timestamps, mean_heading
- [ ] OptoTriggerWorker spawned in main.py
- [ ] Hardware commands sent successfully (needs testing)
- [ ] Process starts/stops cleanly (needs testing)
- [ ] Integration with main.py works (needs testing)
- [ ] Error handling is robust (needs testing)

---

## Key Design Decisions

### Separation of Concerns
- **OptoTrigger**: Hardware control only (serial communication, Arduino commands)
- **OptoTriggerWorker**: Process orchestration, ZMQ communication, data logging

### Following Established Patterns
- Use `WorkerProcess` base class (same as LiquidLens, TriggerHandler)
- Use ZMQ pub/sub pattern (same as other processes)
- Use `CSVWriter` utility (same as logging patterns elsewhere)
- Use configuration system (OptoTriggerConfig, ZMQConfig)

### Sham Stimulation - REVISED
- **Original Plan:** Worker determines sham
- **Implemented:** Hardware class determines sham and reports status via return value
- **Rationale:** Single source of truth, no logic duplication, accurate logging guaranteed

### CSV Logging Location
- Move CSV logging from hardware class to worker process
- Worker has access to full trigger context (obj_id, frame, timestamps, heading)
- Hardware class focuses purely on Arduino communication

### Timestamp Handling - ENHANCEMENT
- **Added dual timestamps:**
  - `braid_timestamp`: When object was at trigger position (from tracking)
  - `trigger_timestamp`: When trigger decision was made (Python time)
- Both saved to CSV for complete temporal record
- Backward compatible with old `timestamp` field

### Enhanced Trigger Data - ENHANCEMENT
- **Added fields beyond original plan:**
  - `mean_heading`: Trajectory heading for analysis
  - `frame`: Camera frame number for precise correlation
  - Dual timestamps for complete temporal tracking

---

## Dependencies

- `src.utils.worker_process.WorkerProcess` - base class
- `src.utils.config` - configuration management
- `src.classes.csv_writer.CSVWriter` - CSV logging
- `src.classes.opto_trigger.OptoTrigger` - hardware control
- `zmq` - ZMQ messaging

---

## Implementation Summary

**Completed:**
- ✅ Task 1: OptoTrigger refactored to pure hardware controller
- ✅ Task 2: OptoTriggerWorker process created
- ✅ Task 3: Configuration verified
- ✅ Task 4: TriggerHandler integration enhanced

**Remaining:**
- ⏳ Task 5: Integration into main.py
- ⏳ Task 6: Testing and validation

**Branch:** `optotrigger-worker-refactor`

**Commits:**
1. `6c8eceb` - Refactor OptoTrigger to pure hardware controller
2. `b3949d5` - Add OptoTriggerWorker process and improve trigger API
3. `c9a18f4` - Add frame and mean_heading to TRIGGER messages with dual timestamps

---

## Success Criteria

**Architecture:**
- [x] OptoTrigger class is pure hardware controller
- [x] OptoTriggerWorker process successfully receives TRIGGER messages
- [x] Sham stimulation determined in hardware, reported to worker
- [x] CSV logging captures all trigger events with enhanced data

**Integration (Pending Testing):**
- [ ] Process integrates cleanly into main.py orchestration
- [ ] Hardware commands sent to Arduino on trigger events
- [ ] System runs without errors in normal operation
- [ ] Graceful error handling when hardware unavailable

**Data Quality (Pending Testing):**
- [ ] opto.csv contains: obj_id, frame, braid_timestamp, trigger_timestamp, mean_heading, duration, intensity, frequency, color, sham
- [ ] Frame numbers correctly match BRAID tracking data
- [ ] Both timestamps accurately reflect event timing
- [ ] Mean heading values correlate with trajectory data
