# Arduino PWM Controller

## Overview
This directory contains the Arduino firmware for the optotrigger system. The firmware receives serial commands from the main Python application and generates precisely timed PWM signals based on the specified parameters.

## Hardware Requirements
- Arduino board (Uno, Nano, or compatible)
- Connection to the PWM-controlled device on pin 9
- USB connection to the host computer

## Installation
1. Open the `.ino` file in the Arduino IDE
2. Select your Arduino board type from Tools > Board
3. Select the correct COM port from Tools > Port
4. Click Upload

## Communication Protocol
The firmware accepts commands via Serial at 115200 baud in the following format:

```
<duration,intensity,frequency>
```

Where:
- `duration`: Time in milliseconds (0-3000) that the PWM signal will be active
- `intensity`: PWM duty cycle (0-255)
- `frequency`: Frequency in Hz. Set to 0 for continuous output

### Examples:
- `<1000,255,0>` - Full intensity for 1 second, continuous signal
- `<500,128,10>` - 50% intensity for 0.5 seconds, pulsing at 10Hz
- `<2000,200,1000>` - ~78% intensity for 2 seconds, pulsing at 1kHz

## Performance Characteristics
- Command parsing: <1ms
- PWM setup time: <0.1ms
- Total processing latency: ~1ms before PWM activation
- Timing accuracy: Microsecond precision for high frequencies

## Pin Configuration
- PWM Output: Pin 9 (configurable in code)

## Debugging
The firmware outputs timing information via Serial (115200 baud) after processing each command:
- Parse time (microseconds)
- Execution time (microseconds)
- Total processing time (microseconds)
- Echo of the processed command

## Integration with Python
The Arduino connects to the main Python application through the `optotrigger.py` module, which handles serial communication and command generation. Ensure the correct serial port is specified in the Python configuration.

## Development Notes
- The PWM frequency is limited by the Arduino's hardware capabilities
- For frequencies >500Hz, microsecond timing is used for improved accuracy
- For frequencies <500Hz, millisecond timing is used
- The debug output can be disabled by commenting out the Serial.print lines for production use

## Troubleshooting

### No Response from Arduino
- Check USB connection
- Verify correct COM port in host software
- Ensure baud rate is set to 115200

### Inaccurate Timing
- For very high frequencies (>10kHz), timing may be less precise
- System load on the Arduino can affect timing accuracy
- Consider disabling debug messages for timing-critical applications

### PWM Signal Issues
- Verify connections to pin 9
- Check that intensity values are within 0-255 range
- Ensure external circuit can handle the PWM frequency specified