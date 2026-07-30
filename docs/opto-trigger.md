# Optogenetic Trigger

Controls LED stimulation via Arduino serial interface.

## Hardware

- Arduino board (Uno, Nano, or compatible)
- Three PWM-capable output pins wired to a PicoBuck (or similar) constant-current LED driver:
  - Pin 5 — Red channel
  - Pin 3 — Green channel
  - Pin 6 — Blue channel
- Pin 9 — Backlight channel (independent of the stimulus LEDs; see the backlight command below)

## Firmware Installation

1. Open `arduino/firmware/opto_trigger/opto_trigger.ino` in the Arduino IDE
2. Select your board: Tools > Board
3. Select the port: Tools > Port
4. Click Upload

## Serial Protocol

Commands are sent at **115200 baud** in the format:

```
<duration,intensity,frequency,color>
```

| Parameter | Range | Description |
|-----------|-------|-------------|
| `duration` | 0–3000 ms | How long the PWM signal is active |
| `intensity` | 0–255 | PWM duty cycle (LED brightness) |
| `frequency` | Hz | Pulse frequency; 0 = continuous output |
| `color` | red, green, blue, white | LED channel selection |

**Examples:**
- `<1000,255,0,red>` — Full-intensity red for 1 second, continuous
- `<500,128,10,green>` — 50% green pulsing at 10 Hz for 0.5 seconds
- `<2000,200,0,white>` — ~78% intensity across all channels for 2 seconds

**Backlight command:**

A second protocol drives the backlight pin (9) directly, with no duration or color:

```
[intensity]
```

`[255]` turns the backlight fully on, `[0]` turns it off (range 0–255). The `OptoTriggerWorker` sends `[255]` when the experiment starts and `[0]` on shutdown, so the arena stays lit for tracking between trials without any manual control.

**Performance:**
- Command parsing: <1 ms
- Total latency before PWM activation: ~1 ms
- Timing accuracy: microsecond precision

## Python Integration

The `OptoTriggerWorker` process (`src/processes/led.py`) handles:
- Subscribing to OPTO_ZONE_ENTER messages from TriggerHandler
- Selecting duration, intensity, and frequency from config lists via **balanced randomization** — it tracks usage counts of every parameter combination and always picks a least-used one (random tie-break), so counts differ by at most 1 across combinations. This is *not* uniform-at-random sampling
- Sham trial support (configurable probability)
- Constructing and sending serial commands
- Logging all stimulation events to `opto.csv` in the Braid recording folder
- Driving the backlight: on (`[255]`) at startup, off (`[0]`) at shutdown

**Always started, even when `active = false`.** The worker runs in *backlight-only* mode when stimulation is disabled (no ZMQ subscription, no `opto.csv`). Hardware-failure semantics depend on the flag: with `active = true` an unopenable Arduino port aborts experiment startup (the worker is a critical process); with `active = false` it logs a warning and the experiment continues without the backlight.

**`opto.csv` columns:**
`obj_id, frame, braid_timestamp, trigger_timestamp, mean_heading, duration, intensity, frequency, color, sham`

**Configuration:**
```toml
[opto_trigger]
active = true
port = "/dev/opto_trigger"
duration = [100, 200, 300]          # ms — randomly selected per trigger
intensity = [0, 51, 102, 153, 204, 255]
frequency = 0                       # Hz — 0 = continuous; list for random selection
color = "red"
sham_probability = 0.0              # 0.0–1.0 fraction of sham (no-light) trials
```

## Bench Testing

Before connecting to live specimens, validate each channel:

1. Open Arduino Serial Monitor at 115200 baud
2. Send each of the following and verify the correct LED illuminates:
   - `<100,64,0,red>` — red only
   - `<100,64,0,green>` — green only
   - `<100,64,0,blue>` — blue only
   - `<100,64,0,white>` — all three
3. Send `<500,128,5,white>` and confirm pulsed output
4. Send `<100,64,0,purple>` and confirm Arduino reports an error (no LEDs fire)
5. Check timing diagnostics in serial output

Document any inter-channel brightness imbalance for future calibration.

## Troubleshooting

**No response from Arduino:**
- Check USB connection
- Verify the correct COM port in `configs/config.toml`
- Confirm baud rate is 115200

**Inaccurate timing at high frequencies:**
- Above 500 Hz the firmware switches from millisecond to microsecond delay loops; at very high frequencies the per-cycle overhead (`analogWrite` + loop) distorts the pulse width
- Minimize serial output during time-critical experiments

**PWM signal issues:**
- Verify wiring to pins 5, 3, 6 (red, green, blue)
- Confirm intensity values are 0–255
- Check LED driver power supply can source requested current
