# Car brain wiring (standalone demo car)

One ESP32 DevKit "car brain" reads three sensors and a button and shows alerts on a 16x2 LCD and a buzzer. It has **no Wi-Fi and no
backend link**: it shows what an in-car unit would do. The ESP32-CAM is a separate board with its own power and no signal wires
to the brain (section 4).

> Status: firmware compiles and its alert logic is host-tested; **not yet wired or bench-tested on a board.** Check each module's
> silkscreen against this table before powering up.

## 1. Car brain: ESP32 DevKit

| Part | Part pin | ESP32 pin | Notes |
|---|---|---|---|
| MPU-9250 | VCC | 3V3 | 3.3 V module, do not use 5 V unless the board says it has a regulator |
| | GND | GND | |
| | SDA | GPIO 21 | |
| | SCL | GPIO 22 | |
| | AD0 / ADO | GND | I2C address 0x68. Leave INT, FSYNC, NCS unconnected |
| 16x2 LCD (I2C backpack) | VCC / VIN | VIN (5 V) | the backpack pulls SDA/SCL up to 5 V, so it gets its own bus |
| | GND | GND | |
| | SDA | GPIO 16 | second I2C bus (`Wire1`), address 0x27 or 0x3F |
| | SCL | GPIO 17 | adjust the blue contrast pot if you see only blocks |
| HC-SR04 | VCC | VIN (5 V) | |
| | GND | GND | |
| | TRIG | GPIO 25 | |
| | ECHO | divider, then GPIO 34 | **never straight in: it outputs 5 V** |
| Push button | leg 1 | GPIO 32 | internal pull-up, pressed = LOW |
| | leg 2 | GND | |
| Passive buzzer | VCC | VIN (5 V) | use 3V3 if it is marked 3.3 V only |
| | GND | GND | |
| | I/O | GPIO 13 | driven with a 2.7 kHz tone; a passive buzzer only clicks on a plain HIGH/LOW |

GPIO 26 and 27 are free (the RGB LED that used them is dropped).

### ECHO voltage divider
```
HC-SR04 ECHO --[ R1 1 kohm ]--+-- GPIO 34
                              |
                         [ R2 2 kohm ]
                              |
                             GND
```
5 V x 2 / (1 + 2) = 3.3 V. No 2 kohm at hand? Use two 1 kohm in series for R2. Colour bands (4-band, 5 %): 1 kohm brown-black-red-gold,
2 kohm red-black-red-gold. Confirm with a multimeter.

## 2. Mounting
* **HC-SR04 on the front** of the car, level and facing forward.
* **MPU-9250 flat and rigid, arrow towards the nose.** The alerts use the acceleration magnitude, so orientation matters little, but
  a loose board gives false hazards.

## 3. What it does (`car` firmware)
| Input | Alert | LCD | Buzzer |
|---|---|---|---|
| HC-SR04 within 100 cm | Obstacle | `Obstacle NN cm` / `Keep distance` | beeps, faster when closer, continuous inside 10 cm |
| MPU `|a|` differs from 1 g by 0.5 g or more | Road hazard (bump / pothole) | `ROAD HAZARD` / `Bump detected` | two short beeps, held 2 s |
| MPU `|a|` at or above 2.5 g | Crash | `CRASH DETECTED` / `Stop safely` | fast two-tone, held 5 s |
| Button press | Emergency alert (demo) | `EMERGENCY ALERT` / `demo: not sent` | siren, held 5 s |

A higher alert is never replaced by a lower one (crash over emergency over hazard over obstacle). The emergency alert is shown and
sounded **locally only**; nothing is transmitted. The thresholds (`HAZARD_DEV_G`, `CRASH_G`) can be changed with `-D` flags in
`firmware/car/platformio.ini` if a toy car is too jumpy or too stiff.

## 4. ESP32-CAM power: can it share the DevKit's 5 V pin?
Yes, it can work, with care. The DevKit's VIN pin carries the USB 5 V (through a small diode, so about 4.5-4.8 V). The CAM's 5V pin
accepts it.

| Option | When | How |
|---|---|---|
| **A. Separate USB cable (recommended default)** | any time | CAM MB board to its own 2 A power-bank port. No shared 5 V. Fewest brown-outs |
| **B. Share the DevKit VIN** | you only have one cable or want a tidy car | DevKit VIN to CAM **5V** pin, GND to GND, 1000 uF across 5V/GND at the CAM (long leg to 5V). Short, thick wires and a good 2 A cable into the DevKit |

Rules for option B:
1. Connect to the CAM's **5V** pin only, never its 3V3 pin.
2. **Never connect the MB board's USB at the same time** while VIN is wired to its 5V pin. Two supplies on one rail fight each other. Unplug the VIN wire when you flash the CAM.
3. When the DevKit is flashed from the laptop with the CAM attached, the laptop's USB port supplies both, which can exceed what a port gives. Flash with the CAM disconnected.
4. Budget: DevKit about 80-250 mA, CAM peaks near 300-400 mA with Wi-Fi, plus sensors: about 700 mA peak. Fine for a power bank, marginal for a laptop port.
5. If the CAM resets (brown-out) or the image tears, go back to option A.
No signal wires are needed between the boards. Camera mounting: front of the car facing the road, tilted slightly down, ribbon cable fully seated with the latch closed. The `camweb` firmware makes the CAM its own Wi-Fi network (`FlowSync-CAM`, page at `http://192.168.4.1/`); the pothole detection runs in the laptop software on saved photos.

## 5. Power
Power bank port 1 to the DevKit (and the CAM in option B), port 2 (2 A) to the CAM MB board in option A. If the bank switches off by
itself because the draw is low, press its button or add a small constant load.

## 6. Bring-up order
1. Flash `cartest` (`pio run -d firmware/car -e cartest -t upload`). The serial monitor at 115200 shows both I2C scans and the sensor stream.
2. MPU-9250 only: expect chip ID 0x71 (0x73 / 0x70 also work).
3. Add the HC-SR04 and divider: `dist` tracks a hand in front of it.
4. Add the button and buzzer: `b` beeps, `z` sweeps tones (silent or only clicks means an active buzzer, build with `-DBUZZER_PASSIVE=0`).
5. Add the LCD: `l` writes test text.
6. Flash `car` for run mode.
7. Flash the ESP32-CAM with `camtest` (diagnostic) then `camweb` and confirm a photo loads.
