# Connected car wiring (main car: ambulance or normal car)

The car carries **two boards**: the ESP32 DevKit "car brain" and the ESP32-CAM. They talk only over Wi-Fi to the backend. They share
GND and (optionally) 5 V, see section 3.

> Status: design only. Not yet wired or tested. Check each module's silkscreen against this table before powering up.

## 1. Car brain: ESP32 DevKit #2

| Part | Part pin | ESP32 pin | Notes |
|---|---|---|---|
| MPU-9250 | VCC | 3V3 | 3.3 V module, do not use 5 V unless the board says it has a regulator |
| | GND | GND | |
| | SDA | GPIO 21 | |
| | SCL | GPIO 22 | |
| | AD0 / ADO | GND | I2C address 0x68. Leave INT, FSYNC, NCS unconnected |
| HC-SR04 | VCC | VIN (5 V) | |
| | GND | GND | |
| | TRIG | GPIO 25 | |
| | ECHO | divider, then GPIO 34 | **never straight in: it outputs 5 V** |
| Push button | leg 1 | GPIO 32 | internal pull-up, pressed = LOW |
| | leg 2 | GND | |
| RGB LED | red | GPIO 26 through R1 | see section 2 |
| | blue | GPIO 27 through R2 | |
| | green | not connected | spare |
| | common (longest leg) | GND | common-cathode type |
| Active buzzer module | VCC | VIN (5 V) | use 3V3 if it is marked 3.3 V only |
| | GND | GND | |
| | I/O | GPIO 13 | many modules are active-low; the test sketch finds out |

### ECHO voltage divider
```
HC-SR04 ECHO ──[ R3 1 kΩ ]──┬── GPIO 34
                            │
                         [ R4 2 kΩ ]
                            │
                           GND
```
5 V × 2 / (1 + 2) = 3.3 V. No 2 kΩ at hand? Use two 1 kΩ in series for R4.

## 2. RGB LED (4 legs) and resistors

### Identify the legs
Hold the LED with the flat edge of the rim on the left. The legs left to right are normally:
**Red, Common (longest), Green, Blue**. Shapes differ between makers, so verify: put a 220 Ω in series with 3.3 V on one leg and touch the
longest leg to GND; the colour that lights tells you the leg. If nothing lights, you have a *common anode* part (longest leg goes to
3V3 instead, and the firmware logic is inverted): tell me and I will flip a setting.

### Resistor values
LED current = (3.3 V − Vf) / R. Typical Vf: red ≈ 2.0 V, green ≈ 2.2–3.0 V, blue ≈ 2.8–3.2 V.

| Ref | Where | Value | Colour bands (4-band, ±5 %) | Colour bands (5-band, ±1 %) | Result |
|---|---|---|---|---|---|
| R1 | RGB red leg → GPIO 26 | 220 Ω | red · red · brown · gold | red · red · black · black · brown | ≈ 6 mA |
| R2 | RGB blue leg → GPIO 27 | 100 Ω (or two 220 Ω in parallel ≈ 110 Ω) | brown · black · brown · gold | brown · black · black · black · brown | ≈ 2–4 mA |
| R3 | ECHO divider top | 1 kΩ | brown · black · red · gold | brown · black · black · brown · brown | |
| R4 | ECHO divider bottom | 2 kΩ | red · black · red · gold | red · black · black · brown · brown | |

Blue needs the lower value because its forward voltage is almost the whole 3.3 V, so a 220 Ω leaves it very dim. If you only have
220 Ω parts, put two in parallel on the blue leg.

Not for the car, for the tabletop controller: 10 kΩ pull-up on OE = brown · black · orange · gold (5-band: brown · black · black · red · brown).

### Colour code cheat sheet
Digit values: black 0, brown 1, red 2, orange 3, yellow 4, green 5, blue 6, violet 7, grey 8, white 9.
4-band: first digit, second digit, multiplier (number of zeros), tolerance (gold ±5 %, silver ±10 %).
5-band: three digits, multiplier, tolerance (brown ±1 %). Read from the end that has the bands bunched together, away from the
gold/brown tolerance band. Always confirm with a multimeter in the ohms range.

## 3. ESP32-CAM power: can it share the DevKit's 5 V pin?

Yes, it can work, with care. The DevKit's VIN pin carries the USB 5 V (through a small diode, so about 4.5–4.8 V). The CAM's
5V pin accepts it.

| Option | When | How |
|---|---|---|
| **A. Separate USB cable (recommended default)** | any time | CAM MB board → its own 2 A power-bank port. No shared 5 V. Fewest brown-outs |
| **B. Share the DevKit VIN** | you only have one cable or want a tidy car | DevKit VIN → CAM **5V** pin, GND → GND, 1000 µF across 5V/GND at the CAM (long leg to 5V). Use short, thick wires and a good 2 A cable into the DevKit |

Rules for option B:
1. Connect to the CAM's **5V** pin only, never its 3V3 pin.
2. **Never connect the MB board's USB at the same time** (for flashing or otherwise) while VIN is wired to its 5V pin. Two supplies on one rail fight each other. Unplug the VIN wire when you flash the CAM.
3. Same applies to the DevKit: when the DevKit is flashed from the laptop and the CAM is attached to VIN, the laptop's USB port supplies both, which can exceed what a laptop port gives. Flash with the CAM disconnected.
4. Budget: DevKit about 80–250 mA with Wi-Fi, CAM peaks near 300–400 mA with Wi-Fi, plus sensors: about 700 mA peak. Fine for a power bank, marginal for a laptop port.
5. If the CAM resets while running (brown-out) or the camera image tears, go back to option A.
No signal wires are needed between the boards in either option.

Camera mounting: front of the car facing the road, tilted slightly down, flash LED off, ribbon cable fully seated with the latch closed.

## 4. Roof marker
Print the ArUco id 20 marker at 100 % scale, laminate it flat on the roof with its **up arrow towards the car's nose**.

## 5. Power
* Power bank port 1 → ESP32 DevKit (and the CAM too in option B). Port 2 (2 A) → CAM MB board in option A.
* If the bank switches off by itself because the draw is low, press its button or add a small constant load.
* Mount the MPU-9250 **flat, rigid, arrow pointing to the nose**; crash detection depends on the axes.

## 6. Bring-up order
1. MPU-9250 only: run the test sketch, expect chip ID 0x71 (0x73 / 0x70 also work, see the sketch output).
2. Add the HC-SR04 and divider: distance should track a hand in front of it.
3. Add the button, RGB LED and buzzer: each is exercised by the test sketch.
4. Flash the ESP32-CAM separately and confirm a frame is captured.
5. Only then add Wi-Fi, the backend and ESP-NOW.
