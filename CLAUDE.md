# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OptoFly is a system for optogenetic stimulation of flies based on their position and movement tracking. It consists of several components:

1. **Tracking data acquisition**: Subscribes to BRAID server tracking data for fly positions
2. **Trigger handling**: Processes tracking data to determine when to trigger stimulation
3. **Hardware control**: Arduino-based optical stimulation and liquid lens control
4. **Visualization**: Real-time visualization of tracking data and triggers

## Project Structure

- **src/**: Main Python source code
  - **classes/**: Core hardware controller classes
  - **processes/**: Multi-process system components
  - **utils/**: Shared utility code
- **arduino/**: Arduino firmware for hardware control
- **calibrations/**: Calibration data files

## Architecture

The system uses a multi-process architecture with ZeroMQ for inter-process communication:

1. **BraidSubscriber**: Connects to BRAID server to get fly tracking data
2. **TriggerHandler**: Processes tracking data and generates trigger signals
3. **OptoTrigger**: Controls Arduino-based optical stimulation
4. **LiquidLens**: Controls liquid lens focus based on target distance
5. **Visualization**: Real-time visualization of system state

Each component runs in its own process, inheriting from the `WorkerProcess` base class.

## Development Commands

### Installation

```bash
# Install dependencies using uv (Python package manager)
uv pip install .

# Install development dependencies
uv pip install .[dev]
```

### Running the System

```bash
# Run the main application
python main.py

# Run individual components
python -m src.processes.braid_subscriber
python -m src.processes.trigger_handler
```

### Code Quality

```bash
# Run linting with ruff
ruff check .

# Format code with ruff
ruff format .
```

## Configuration

The system is configured using a TOML file at the project root (`config.toml`). It defines:

- ZMQ communication settings
- Hardware parameters
- Trigger thresholds and behavior
- Logging configuration

## Hardware Integration

The project interfaces with two main hardware components:

1. **OptoTrigger**: Arduino that generates PWM signals for optical stimulation
2. **Liquid Lens**: Adjustable focus lens for tracking subjects at different distances

Communication with Arduino hardware is via serial connections with specified commands.

## Common Development Tasks

1. **Adding a new process**: Create a new class in `src/processes/` that inherits from `WorkerProcess`.
2. **Modifying trigger behavior**: Edit the logic in `TriggerHandler._evaluate_triggers()`.
3. **Calibrating hardware**: Update the calibration files in `calibrations/`.
4. **Debugging**: Set the log level to `DEBUG` in config.toml or via command-line args.

## Testing

Individual components can be tested directly by running their respective modules:

```bash
# Test the opto trigger hardware
python -m src.classes.opto_trigger

# Test the trigger handler process
python -m src.processes.trigger_handler
```