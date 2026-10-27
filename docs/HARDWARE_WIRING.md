# Intersection controller wiring (supersedes HW spec §4.1 / §4.3 / §4.4)

One ESP32 DevKit drives all 18 traffic LEDs through **three daisy-chained 74HCT595** shift registers (one per
intersection, 6 LEDs each). This replaces the old 9-GPIO direct-drive map, which could not drive 18 LEDs and used boot-strapping pins.

> Status: design + firmware core verified on a PC. **Not yet wired or tested on real hardware** — verify against your exact
> DevKit pinout and 74HCT595 datasheet before powering up.

## ESP32 pins

| Signal | GPIO | Goes to | Notes |
|---|---|---|---|
| DATA | 23 | 595 #1 pin 14 (SER) | |
| CLOCK | 18 | pin 11 (SRCLK) on all three | |
| LATCH | 19 | pin 12 (RCLK) on all three | all 18 LEDs update at once |
| OE | 21 | pin 13 (OE, active LOW) on all three | **10 kΩ pull-up to 3V3** |

None of these are strapping pins (0, 2, 5, 12, 15 avoided) or WROVER PSRAM pins (16, 17). Pins 34/35/36/39 are input-only and
reserved for the Tier-2 ultrasonic ECHO lines (TRIG 4/13/14 via dividers).

## 74HCT595 (DIP-16) — every chip

| Pin | Connect to |
|---|---|
| 16 Vcc | **5 V** + 0.1 µF to GND, close to the chip (HCT inputs accept 3.3 V logic: V_IH = 2.0 V) |
| 8 GND | GND (common with the ESP32) |
| 10 SRCLR | **5 V** (never leave floating) |
| 11 SRCLK / 12 RCLK / 13 OE | from the ESP32 as above (shared by the three chips) |
| 14 SER | #1: GPIO 23 · #2: pin 9 of #1 · #3: pin 9 of #2 |
| 9 Q7' | to SER (pin 14) of the next chip; leave open on #3 |

Chip #1 = intersection **I1**, #2 = **I2**, #3 = **I3** (the chip nearest the ESP32 is I1). The firmware shifts I3's byte first.

## LED outputs — same on all three chips

| 595 output | DIP pin | Lamp | Resistor (5 V) |
|---|---|---|---|
| Q0 | 15 | Movement A — Red | 330 Ω |
| Q1 | 1 | Movement A — Yellow | 330 Ω |
| Q2 | 2 | Movement A — Green | 150–220 Ω |
| Q3 | 3 | Movement B — Red | 330 Ω |
| Q4 | 4 | Movement B — Yellow | 330 Ω |
| Q5 | 5 | Movement B — Green | 150–220 Ω |
| Q6, Q7 | 6, 7 | unused — leave open | |

Wiring per LED: `Qx → resistor → LED anode, LED cathode → GND`. That is 18 LEDs + 18 resistors.

### Why these values
* The 595 runs at 5 V so green LEDs (V_f up to ~3 V) get usable current; at 3.3 V they would barely light. Measure V_f of
  *your* LEDs and aim for 5–10 mA each (≤ 20 mA/pin, ≤ 70 mA/package). At most **two LEDs per chip** are lit at any time
  (one per movement), so each chip stays below ~20 mA.
* **OE pull-up (to 3V3, not 5 V)** keeps every output high-impedance (LEDs dark) while the ESP32 resets or boots. Firmware latches
  *all-red* first and only then pulls OE low, so there is no moment with an undefined lamp pattern. A 5 V pull-up would push
  current into the 3.3 V-only GPIO.
* One latch pulse updates all LEDs together, so no transient conflicting green can appear while bits are shifting.

## Power
Power the 595s and LEDs from the 5 V rail of the same supply as the ESP32 (USB hub / power bank), share **GND**, add ~100 µF near
the chips. Budget well under 200 mA for all LEDs. Do not power anything from GPIO pins.

## Network (most common demo failure)
* ESP32 is **2.4 GHz only**. Use a dedicated router/hotspot: 2.4 GHz, WPA2 (not WPA3-only), fixed channel 1/6/11, no client isolation.
* Give the laptop a DHCP reservation (or use its mDNS name) and put it in `BACKEND_HOST`.
* Start the backend on the LAN: `uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000`, and allow inbound TCP 8000 in the Windows firewall for the private network.
* Use real device keys: edit `config/devices.yaml` (and the same key in `firmware/intersection/include/config.h`).

## Bring-up order (HW spec §10, steps 2–5)
1. Build `pio run -e lamptest -t upload` — lights **one LED at a time** in order I1 A_R, A_Y, A_G, B_R, B_Y, B_G, then I2, I3. Check polarity, colours, brightness, and that the order matches the table (swapped chips ⇒ swapped intersections).
2. Power-cycle with *no* firmware running (or hold EN low): all LEDs must stay **off** (OE pull-up works).
3. Flash the main firmware with the backend **off**: boot shows all-red, then after 5 s the fixed-time fallback alternates A/B every 8 s per intersection, always through yellow + 1 s all-red.
4. Start the backend + camera/simulator: the dashboard should show device reports in sync and commands acknowledged; kill the backend and confirm fallback within ~5 s; restart and confirm recovery.
