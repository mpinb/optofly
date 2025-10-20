# OptoTrigger Worker Implementation Plan

## Overview

Refactor the OptoTrigger architecture to separate hardware control from process orchestration and data logging, following the established pattern used by LiquidLens.

## Current State

- `OptoTrigger` class mixes hardware control with CSV logging
- No worker process exists to integrate OptoTrigger into the ZMQ pipeline
- TriggerHandler publishes TRIGGER messages that are only consumed by LiquidLens
- CSV logging is embedded in the hardware class

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

## Implementation Tasks

### Task 1: Refactor OptoTrigger Class (Pure Hardware)

**File:** `src/classes/opto_trigger.py`

**Changes:**
- Remove `braid_folder` parameter from `__init__()`
- Remove `CSVWriter` import and initialization
- Remove all CSV logging from `trigger()` method (lines 103-113, 129, 147)
- Remove `csv_writer.close()` from `close()` method (lines 192-195)
- Update `trigger()` signature to remove `obj_id`, `frame`, `timestamp` parameters
- Simplify `trigger()` to accept only `sham` parameter (optional)
- Update docstrings to reflect hardware-only responsibility
- Update `__main__` CLI to work without CSV logging

**Result:** OptoTrigger becomes a pure hardware controller like LensDriver

### Task 2: Create OptoTriggerWorker Process

**File:** `src/processes/opto_trigger_worker.py` (NEW)

**Requirements:**
- Inherit from `WorkerProcess` (import from `src.utils.worker_process`)
- Subscribe to TRIGGER topic via ZMQ (similar to LiquidLens lines 210-216)
- Initialize OptoTrigger hardware controller
- Initialize CSVWriter for `opto.csv` logging
- Implement `initialize()` method to set up ZMQ, hardware, and CSV writer
- Implement `run()` method with main event loop
- Implement `close()` method to clean up resources

**ZMQ Configuration:**
- Use `ZMQConfig` to get subscriber address and trigger port
- Subscribe to `trigger_topic` ("TRIGGER")
- Parse messages in format: `{"obj_id": int, "frame": int, "timestamp": int, ...}`

**CSV Logging:**
- Log to `opto.csv` in the braid folder
- Columns: `obj_id`, `frame`, `timestamp`, `duration`, `intensity`, `frequency`, `color`, `sham`
- Write row after each trigger event (both real and sham)

**Main Loop Logic:**
1. Wait for TRIGGER messages from ZMQ
2. Parse message to extract `obj_id`, `frame`, `timestamp`
3. Determine if sham (using config probability or override)
4. Call `opto_trigger.trigger(sham=sham)`
5. Log event to CSV with all parameters
6. Handle errors gracefully and continue running
7. Exit on stop_event

### Task 3: Update Configuration

**File:** `config.toml`

**Verify/Add:**
- Ensure `[opto_trigger]` section has `active` flag
- Verify all necessary parameters exist (port, baudrate, duration, intensity, frequency, color, sham_probability)
- Document any new configuration needs

**File:** `src/utils/config.py`

**Verify:**
- `OptoTriggerConfig` dataclass has all needed fields
- Configuration loading works correctly
- No changes needed (verify only)

### Task 4: Update TriggerHandler (Optional Enhancement)

**File:** `src/processes/trigger_handler.py`

**Review:**
- Verify TRIGGER messages include all required fields: `obj_id`, `frame`, `timestamp`
- Check `_send_trigger()` method (lines 452-472)
- Ensure message format is compatible with OptoTriggerWorker expectations
- Add any missing fields if needed

### Task 5: Integrate OptoTriggerWorker into Main Process

**File:** `main.py`

**Changes:**
- Import `OptoTriggerWorker` from `src.processes.opto_trigger_worker`
- Check `opto_trigger.active` config flag
- If active, spawn OptoTriggerWorker process alongside other processes
- Pass appropriate parameters (event, config_path, process_name, log_level, log_color)
- Add to process list for cleanup on shutdown

**Pattern to Follow:**
Look at how LiquidLens is spawned and managed in main.py

### Task 6: Testing and Validation

**Manual Testing:**
1. Test OptoTrigger class standalone (using `python -m src.classes.opto_trigger`)
2. Test OptoTriggerWorker process standalone (using `python -m src.processes.opto_trigger_worker`)
3. Test full system integration (using `python main.py`)
4. Verify CSV logging works correctly
5. Verify trigger messages are received and processed
6. Test sham stimulation logic
7. Test error handling and recovery

**Test Scenarios:**
- Normal trigger with real stimulation
- Sham trigger (no hardware activation)
- Missing Arduino hardware (graceful degradation)
- Multiple rapid triggers
- Process shutdown and cleanup
- CSV file creation and writing

**Validation Checklist:**
- [ ] OptoTrigger class has no CSV logging
- [ ] OptoTrigger works without braid_folder
- [ ] OptoTriggerWorker subscribes to TRIGGER topic
- [ ] CSV logging works in worker process
- [ ] opto.csv has all required columns
- [ ] Sham stimulation logged correctly
- [ ] Hardware commands sent successfully
- [ ] Process starts/stops cleanly
- [ ] Integration with main.py works
- [ ] Error handling is robust

## Implementation Order

1. **Task 1** - Refactor OptoTrigger (foundation)
2. **Task 2** - Create OptoTriggerWorker (core functionality)
3. **Task 3** - Verify configuration (infrastructure)
4. **Task 4** - Review TriggerHandler (integration check)
5. **Task 5** - Integrate into main.py (system integration)
6. **Task 6** - Testing and validation (verification)

## Key Design Decisions

### Separation of Concerns
- **OptoTrigger**: Hardware control only (serial communication, Arduino commands)
- **OptoTriggerWorker**: Process orchestration, ZMQ communication, data logging

### Following Established Patterns
- Use `WorkerProcess` base class (same as LiquidLens, TriggerHandler)
- Use ZMQ pub/sub pattern (same as other processes)
- Use `CSVWriter` utility (same as logging patterns elsewhere)
- Use configuration system (OptoTriggerConfig, ZMQConfig)

### CSV Logging Location
- Move CSV logging from hardware class to worker process
- Worker has access to trigger context (obj_id, frame, timestamp)
- Hardware class focuses purely on Arduino communication

### Sham Stimulation
- Worker determines if trigger should be sham
- Hardware class executes sham logic (skip serial command)
- Both real and sham events logged to CSV

## Dependencies

- `src.utils.worker_process.WorkerProcess` - base class
- `src.utils.config` - configuration management
- `src.classes.csv_writer.CSVWriter` - CSV logging
- `src.classes.opto_trigger.OptoTrigger` - hardware control
- `zmq` - ZMQ messaging

## Notes

- OptoTrigger can still be tested standalone via CLI
- Worker process respects `opto_trigger.active` config flag
- CSV file created in braid_folder (passed to worker)
- Worker process uses same logging patterns as other processes
- Hardware initialization failures should be logged but not crash the process

## Success Criteria

- [ ] OptoTrigger class is pure hardware controller
- [ ] OptoTriggerWorker process successfully receives TRIGGER messages
- [ ] Hardware commands sent to Arduino on trigger events
- [ ] CSV logging captures all trigger events with complete data
- [ ] Sham stimulation works correctly
- [ ] Process integrates cleanly into main.py orchestration
- [ ] System runs without errors in normal operation
- [ ] Graceful error handling when hardware unavailable
