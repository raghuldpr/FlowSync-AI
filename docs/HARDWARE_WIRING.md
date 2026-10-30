# Tabletop wiring: two ESP32 DevKits, six traffic modules

The tabletop is a **standalone traffic-light demo**: two ESP32 DevKits light six 4-pin traffic modules (GND, R, Y, G) straight from
their GPIO pins. There is no camera, no ArUco marker and no toy car on the table, and no shift register. The table runs a built-in
cycle plus an ambulance-priority sequence with no Wi-Fi, so the hotspot cannot break it. The software demo (phones, backend, SUMO)
is separate and is not wired to the table. This replaces the earlier one-ESP32 + 74HCT595 design. The backend-connected controller
(`firmware/intersection`) still exists as future work.

> Status: firmware compiles and its logic is host-tested; **wiring and the lamps have not yet been checked on a board.** Bench-test
> every module with the `board1_test` / `board2_test` firmware (section 4) before gluing anything.

**Part check:** the SN74LS95N chips that were bought are not usable here (no latch, no output enable, they cannot drive the modules) and are not part of the build.
**Layout:** board 45 x 29 cm (0.55 mm per layout unit): I1 and I2 on top, I3 below, West, East and South external roads about 2.2 cm wide.

## 1. Which module is which

| Board | Module | Lamp | Role |
|---|---|---|---|
| ESP32 #1 | M1 | I1-A | intersection I1, movement A (ring) |
| ESP32 #1 | M2 | I1-B | I1, movement B (external road) |
| ESP32 #1 | M3 | I2-A | I2, movement A |
| ESP32 #1 | M4 | I2-B | I2, movement B |
| ESP32 #2 | M1 | I3-A | I3, movement A |
| ESP32 #2 | M2 | I3-B | I3, movement B |

## 2. Pins (R / Y / G)

| Board | Module | Red | Yellow | Green |
|---|---|---|---|---|
| ESP32 #1 | M1 (I1-A) | D23 | D22 | D21 |
| ESP32 #1 | M2 (I1-B) | D19 | D18 | D27 |
| ESP32 #1 | M3 (I2-A) | D26 | D25 | D33 |
| ESP32 #1 | M4 (I2-B) | D32 | D14 | D13 |
| ESP32 #2 | M1 (I3-A) | D23 | D22 | D21 |
| ESP32 #2 | M2 (I3-B) | D19 | D18 | D27 |

* **Ground:** every module's GND goes to one breadboard ground rail, joined to an ESP32 GND pin. Join the two boards' grounds too if
  they share one power supply.
* **Avoid** TX0 (D1), RX0 (D3) and D12 (a boot-strapping pin: a lamp on it can stop the board from booting).
* **No resistors:** the modules lit directly from the pins in the bench test (one green is dimmer but fine). Do not power anything
  from a GPIO pin other than these lamps, and keep each module to one lit lamp at a time (the firmware does).
* **BOOT button** (GPIO0, on the DevKit): starts the ambulance sequence now. Press it on both boards together to keep them in step.

## 3. Mounting
Modules lie flat beside the roads, outside the lanes, with the red, yellow and green dots running along each road, red at the same
end on all six. Drill a 5-6 mm hole beside each module for its leads. Mount the ESP32s and breadboard under the board.

## 4. Bench test before the demo (`board1_test`, `board2_test`)
```
pio run -d firmware/table -e board1_test -t upload ; pio device monitor -d firmware/table
```
Serial commands (115200): `h` help, `m` pin map, `l` walk every lamp one at a time (R, Y, G of each module in order), `r` / `y` / `g`
all lamps that colour (wiring check only), `o` all red, `c` 10 s safe A/B cycle. Expected: `l` lights exactly one lamp per second and
prints its name and pin. Fix any module that lights the wrong colour by swapping its wires, then flash the real firmware (`board1` /
`board2`). Same for board 2 with `board2_test`.

## 5. What the demo does (`board1`, `board2`)
* Normal cycle: each intersection alternates A / B with min green, yellow, all-red (timings generated from `config/topology.yaml`),
  always through yellow and a 1 s all-red, never two axes lit at once. This is the same state machine as the backend (`signal_fsm`).
* Ambulance priority: every 50 s loop, from 36 s to 50 s, I1 holds B green and I2 holds A green (the West to East corridor), still
  through yellow and all-red. I3 keeps cycling. The BOOT button starts the same 14 s sequence immediately.
* This is a scripted demonstration of the behaviour. The software simulation shows how it is decided.
