# FlowSync AI — Hardware specification v2

Status: **design baseline**. The firmware core is verified on a PC against the Python reference and the ESP32 application
**compiles** for the real target (`esp32dev`, `lamptest`; RAM 13.7 %, flash 60 %). Nothing is wired or flashed yet — treat every
electrical value as a design intent to be checked against your exact parts. Supersedes `hardware.md` v1.0.

## 1. Architecture (what changed from v1.0 and why)
* **The backend decides, the device executes.** All control flows over signed HTTP/JSON to the laptop. v1.0's direct ESP-NOW
  car → intersection path is **dropped**: it bypassed the deterministic backend validation the brief requires. The firmware is the
  final safety gate for *how* a change happens (min green, yellow, all-red, command freshness, replay rejection) and runs a
  fixed-time cycle if the backend is unreachable for 5 s.
* **The connected car is the emergency vehicle.** It carries a printed ArUco marker (nose direction = marker "up"); the overhead
  phone camera localises it. Car electronics (button, LEDs, buzzer) are optional: an operator can declare the EV from the dashboard.
  *Car firmware is not written yet.*
* **One ESP32 drives all three intersections** (18 lamps) through three daisy-chained 74HCT595 registers (3 GPIOs instead of 18).

## 2. Review findings resolved here
| Finding | Resolution |
|---|---|
| C1 18 LEDs need more pins than exist | 3× 74HCT595 @ 5 V, OE pulled up so outputs are off at power-up; firmware latches all-red *then* enables outputs |
| C5 two control authorities | backend authoritative; ESP-NOW removed |
| C6 car cannot share the table's power / too small | car has its own power bank; stationary electronics share the table supply; car platform ≥ 15 cm or "hand-pushed along marked lanes" (declared in the demo) |
| C7 no localisation of the physical EV | ArUco marker + overhead camera + homography from four corner markers |
| H1 undefined layout, no alternative route | triangle ring (every origin–destination pair has two paths); camera ROIs on every link give downstream queues |
| H4 Wi-Fi pitfalls | dedicated 2.4 GHz WPA2 router/hotspot, fixed channel, DHCP reservation, firewall rule (see `HARDWARE_WIRING.md`) |
| H5 no synchronised clocks | commands carry `seq` and `valid_for_ms`, measured on the device's own `millis()`; random boot id; backend stamps receive time |
| H6 blocking firmware | signal task (core 1, 20 ms, task watchdog) separate from the network task (core 0); no blocking calls in the signal path |
| H13 ultrasonic sensors without a role | moved to Tier 2 (stop-line presence cross-check); not in the base build |
| M3 dim green LEDs on 3.3 V | LEDs driven from the 5 V rail; green 150–220 Ω, red/yellow 330 Ω |
| M4 optional clearance | yellow + 1 s all-red are mandatory before *every* change of green, including emergency priority |
| M5 buzzer drive | (car, optional) GPIO → 1 kΩ → NPN base, buzzer on 5 V, flyback diode for a magnetic buzzer |

## 3. Bill of materials (estimates in INR, to be recomputed from your cart)
| # | Item | Qty | Est. subtotal ₹ |
|---|---|---|---|
| 1 | ESP32 DevKit (WROOM) — intersection controller | 1 | 350–650 |
| 2 | 74HCT595 (DIP-16) | 3 | 45–120 |
| 3 | LEDs: 6 red, 6 yellow, 6 green | 18 | 36–90 |
| 4 | Resistors: 330 Ω ×12, 150–220 Ω ×6, 10 kΩ ×1 | set | 20–60 |
| 5 | Capacitors: 0.1 µF ×3, 100 µF ×1 | set | 15–40 |
| 6 | Breadboards / perfboard | 2 | 160–360 |
| 7 | Jumper wires and connectors | set | 100–250 |
| 8 | Rigid overhead phone mount (phone assumed owned) | 1 | 0–500 |
| 9 | Printed ArUco markers: ids 10–13 (corners), 20 (car), laminated | 5 | 20–80 |
| 10 | Micro toy cars (one carries the marker) | 6 | 180–600 |
| 11 | Table board, road paint/tape, craft materials | set | 200–600 |
| 12 | 2.4 GHz router or hotspot (reuse if possible) | 1 | 0–800 |
| 13 | Power bank for the table electronics | 1 | 800–1500 |
| 14 | Powered USB hub | 1 | 250–600 |
| 15 | USB data cables | 2–4 | 100–400 |
| | **Essential total** | | **2,276–6,650** |
| O1 | ESP32-CAM + programmer (pothole images — Tier 2) | 1 | 450–900 |
| O2 | HC-SR04 ×3 + Echo dividers (Tier 2) | 3 | 150–390 |
| O3 | Car ESP32 + button, 2 LEDs, buzzer, NPN, diode (optional) | 1 | 410–800 |
| O4 | Small power bank for the car | 1 | 400–900 |
| | **With all optional items** | | **3,686–9,640** |

Dropped from v1.0: MPU6050 (motion anomaly is Tier 3), the car HC-SR04, the separate shared-power-bank scheme for the car.
Prices are planning estimates, not quotes.

## 4. Electrical design
* Pin map, 595 pinout, LED mapping, resistor values, OE pull-up and bring-up order: **`HARDWARE_WIRING.md`** (authoritative).
* Controller pins: DATA 23, CLOCK 18, LATCH 19, OE 21 (active low, 10 kΩ pull-up to **3V3**). No strapping pins, no PSRAM pins.
* At most two lamps per register are lit at once (one per movement) → well within the 595's current limits.
* Do not power loads from GPIO. All grounds common. Add 0.1 µF at every chip, ~100 µF on the 5 V rail.

## 5. Signal rules the hardware must honour
Two movements per intersection (A = ring axis, B = external axis), conflict matrix "all movements conflict".
Phase order GREEN → YELLOW (2 s) → ALL-RED (1 s) → GREEN(other). Minimum green 5 s. Fallback cycle 8 s per axis. Values come from
`config/topology.yaml`; `timing_defaults.h` is generated from it and a test fails on drift.

## 6. Camera and markers
Overhead phone, rigid mount, exposure/focus/white balance locked, all four corner markers in view. Draw the table in the layout
frame of `topology.yaml` and print markers at 100 % scale — **`VISION_SETUP.md`**. Lanes hold only 2–6 toy cars: set per-approach
capacities with `python -m app.vision.capacity`.

## 7. Network
2.4 GHz only, WPA2, fixed channel 1/6/11, no client isolation, never the venue Wi-Fi. Backend listens on `0.0.0.0:8000`; allow TCP 8000.
Devices authenticate with an allow-listed id and an HMAC over `method|path|device|boot|seq|body`; replays and old boot ids are rejected.

## 8. Test sequence
1. `lamptest` image: one LED at a time (polarity, order, brightness). 2. Power-cycle without firmware: all LEDs stay off.
3. Main image, backend off: all-red → after 5 s fixed-time fallback, always through yellow + all-red. 4. Backend + simulator/camera:
reports in sync, acks accepted; kill the backend → fallback within ~5 s; restart → recovery. 5. Camera reference capture, then the
`Camera feed lost` and `Controller offline` drills. 6. Full rehearsal of the scenario in physical mode.

## 9. Known limits
Tabletop demonstrator, not a certified controller. Ultrasonic readings (when added) are proximity only. The ESP32 application and
the electrical design are unverified on hardware. Range, power and brown-out behaviour must be measured with the real parts.
