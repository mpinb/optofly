# Arduino RGB OptoTrigger Controller

## Overview
This directory contains the Arduino firmware for the optogenetic trigger system. The firmware receives serial commands from the main Python application and generates precisely timed PWM signals on dedicated RGB channels wired through a PicoBuck (or similar) constant-current driver.

## Hardware Requirements
- Arduino board (Uno, Nano, or compatible)
- Three PWM-capable output pins wired to the LED driver
  - Pin 9 → Red channel
  - Pin 10 → Green channel
  - Pin 11 → Blue channel
- USB connection to the host computer

## Installation
1. Open the `.ino` file in the Arduino IDE.
2. Select your Arduino board type from Tools → Board.
3. Select the correct COM port from Tools → Port.
4. Click Upload.

## Communication Protocol
The firmware accepts commands via Serial at 115200 baud in the following format:

```
<duration,intensity,frequency,color>
```

Where:
- `duration`: Time in milliseconds (0–3000) that the PWM signal will be active.
- `intensity`: PWM duty cycle (0–255) applied to the selected channels.
- `frequency`: Frequency in Hz. Set to 0 for continuous output.
- `color`: LED channel selection (`red`, `green`, `blue`, or `white` to energize all channels).

### Examples
- `<1000,255,0,red>` — Full-intensity red light for 1 second, continuous signal.
- `<500,128,10,green>` — 50% intensity green pulses at 10 Hz for 0.5 seconds.
- `<2000,200,1000,white>` — ~78% duty cycle across RGB channels pulsing at 1 kHz for 2 seconds.

## Performance Characteristics
- Command parsing: <1 ms.
- PWM setup time: <0.1 ms.
- Total processing latency: ~1 ms before PWM activation.
- Timing accuracy: Microsecond precision for high frequencies.

## Pin Configuration
- Red PWM Output: Pin 9.
- Green PWM Output: Pin 10.
- Blue PWM Output: Pin 11.

## Debugging
The firmware outputs timing information via Serial (115200 baud) after processing each command:
- Parse time (microseconds).
- Execution time (microseconds).
- Total processing time (microseconds).
- Echo of the processed command (including the resolved color).

## Integration with Python
The Arduino connects to the main Python application through the `OptoTrigger` class, which validates color selections, constructs the serial payloads, and logs stimulation metadata (including `color`) to `opto.csv`. Ensure `config.toml` specifies the intended color under `[opto_trigger]`.

## Bench Testing Checklist
Before connecting to live specimens, validate RGB behaviour on the bench:

1. Power the PicoBuck (or LED channels) and connect the Arduino via USB.
2. Open the Arduino Serial Monitor (115200 baud, newline terminated) or another serial terminal.
3. Issue:
   - `<100,64,0,red>` — verify only the red LED illuminates.
   - `<100,64,0,green>` — verify only the green LED illuminates.
   - `<100,64,0,blue>` — verify only the blue LED illuminates.
   - `<100,64,0,white>` — verify all three LEDs illuminate with similar brightness.
4. Trigger `<500,128,5,white>` and confirm pulsed output across all channels.
5. Send an invalid command such as `<100,64,0,purple>` and confirm the Arduino reports an error without driving any LEDs.
6. Observe timing diagnostics to confirm processing latency remains within expectations.

Document observed intensities and any inter-channel imbalance for future calibration updates.

## Troubleshooting

### No Response from Arduino
- Check the USB connection.
- Verify the correct COM port in host software.
- Ensure the baud rate is set to 115200.

### Inaccurate Timing
- For very high frequencies (>10 kHz), timing may be less precise.
- Background serial traffic can affect timing; minimize unnecessary prints for time-critical experiments.

### PWM Signal Issues
- Verify wiring to pins 9, 10, and 11.
- Check that intensity values are within the 0–255 range.
- Ensure the LED driver and power supply can source the requested current for all active channels.
