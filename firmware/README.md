# Firmware

Four projects. **The demo hardware is the first three, all standalone (no Wi-Fi, no backend).** `intersection/` and `roadcam/` are the
backend-connected versions, kept as future work.

| Project | Board | What it is | Environments |
|---|---|---|---|
| `table/` | 2x ESP32 DevKit | tabletop traffic lights: built-in cycle + ambulance corridor, 6 modules lit from pins. Wiring: [HARDWARE_WIRING.md](../docs/HARDWARE_WIRING.md) | `board1`, `board2`, `board1_test`, `board2_test` |
| `car/` | ESP32 DevKit | car brain: distance / bump / crash / button alerts on an LCD + passive buzzer. Wiring: [HARDWARE_CAR_WIRING.md](../docs/HARDWARE_CAR_WIRING.md) | `car`, `cartest` |
| `camtest/` | ESP32-CAM | `camtest` diagnostic; `camweb` standalone web camera on its own Wi-Fi (`FlowSync-CAM` / `flowsync1`, `http://192.168.4.1/`) | `camtest`, `camweb` |
| `intersection/` | ESP32 DevKit | backend-connected controller with signed requests (future work for the new table) | `esp32dev`, `lamptest` |
| `roadcam/` | ESP32-CAM | backend-connected road camera (future work) | `esp32cam` |

## Build and flash (needs PlatformIO)
```
pio run -d firmware/table -e board1 -t upload        # then -e board2 on the second DevKit
pio run -d firmware/car -e car -t upload
pio run -d firmware/camtest -e camweb -t upload
pio device monitor -d firmware/<project>              # 115200 baud
```
Compile-only check of everything (no hardware needed): `pio run -d firmware/table -e board1` etc. `intersection` and `roadcam` need
`include/config.h` copied from `config.example.h` first (git-ignored).

## Host tests (no hardware, no ESP32 toolchain; any g++ or clang++)
```
python firmware/table/test/run_host_tests.py     # signal safety, corridor priority, button, clock wrap
python firmware/car/test/run_host_tests.py       # alert thresholds, latching, button debounce, buzzer patterns
pytest backend/tests/test_firmware_core.py       # intersection core vs the Python reference (needs pip install ziglang)
```

## Bench-test checklist (do this before the demo day)
Serial monitor at 115200. Tick each line only after seeing it.

**Table, each DevKit** (`board1_test` / `board2_test`)
- [ ] boot prints the pin map; `l` lights exactly one lamp per second and prints its name (`I1-A RED (D23)` ...)
- [ ] every module shows red, yellow, green in order and the name matches the module you are looking at
- [ ] `r`, `y`, `g` light all lamps that colour (wiring check only); `o` leaves everything red
- [ ] flash `board1` / `board2`: boot prints `FlowSync table demo (standalone)`, lines like `[12345 ms] I1 A=G B=R` appear
- [ ] never two directions lit at once; every green passes through yellow then ~1 s all-red
- [ ] BOOT button prints `[button] ambulance sequence requested`, then `[ambulance] priority sequence ON`; I1 (B) and I2 (A) are green for the corridor for 14 s

**Car** (`cartest`, then `car`)
- [ ] `i` shows the MPU at 0x68 on the MPU bus and the LCD at 0x27 or 0x3F on the LCD bus
- [ ] `[mpu] WHO_AM_I = 0x71` (0x73 / 0x70 / 0x68 also work); a boot beep sounds
- [ ] `s` stream: `dist` follows a hand; `btn PRESSED` when the button is held; `|a|` is about 1.00 at rest
- [ ] `b` three beeps; `z` four different pitches (clicks only = active buzzer: build with `-DBUZZER_PASSIVE=0`)
- [ ] `l` writes text on the LCD (blocks only: turn the contrast pot)
- [ ] `car` run mode: hand at 30 cm gives `Obstacle 30cm` + beeps; a tap gives `ROAD HAZARD` + two beeps; a hard shake gives `CRASH DETECTED`; the button gives `EMERGENCY ALERT` + siren and `demo: not sent`

**ESP32-CAM**
- [ ] `camtest`: `PID=0x26` (OV2640), `JPEG header (FFD8): yes`
- [ ] `camweb`: join Wi-Fi `FlowSync-CAM`, open `http://192.168.4.1/`, "Take photo" shows a fresh picture, the monitor prints `[cam] photo N: ... bytes`

**If an upload fails** ("Failed to communicate with the flash chip"): try another cable and port, hold BOOT while it connects, disconnect
everything from the pins (GPIO 0, 2, 12, 15 especially), add 10 uF between EN and GND, or try `--no-stub`.

## What is and isn't verified
* **Verified on a PC / by compiling:** all five projects build with no warnings from our code; the table, car and intersection logic is host-tested.
* **NOT verified on a board:** any wiring, any lamp, the LCD driver (a minimal PCF8574 driver written for this repo), the passive-buzzer tones,
  the MPU / HC-SR04 readings and the alert thresholds (tuned only by reasoning, not on the toy car), the camera capture, and the Wi-Fi access point.
* **Not implemented:** the table and car do not talk to the backend; the car's emergency alert is local only; OTA updates, ESP-NOW (dropped by design).

---

## `intersection/` (backend-connected controller, kept for future work)
Original notes follow. It still targets one ESP32 with 3x 74HCT595 (the earlier table design).


```
intersection/
  lib/flowsync_core/   portable C++11, no Arduino headers (same code runs on the ESP32 and on a PC)
    signal_fsm.*       port of backend/app/core/fsm.py: GREEN→YELLOW→ALL_RED→GREEN, min green, command expiry on the DEVICE clock,
                       replay rejection (seq), fixed-time fallback when the backend is lost
    sha256.* signing.* HMAC-SHA256 request signing, byte-identical to backend/app/security.py::sign
    protocol.*         JSON for /api/device/{state,commands,command-ack} (tolerant parser, bounded builders)
    leds595.*          74HCT595 chain encoder + bit-serial writer (single latch, all-red first)
    timing_defaults.h  GENERATED from config/topology.yaml (python firmware/tools/gen_timing.py)
  src/main.cpp         ESP32 app: signal task (core 1, 20 ms, watchdog) + net task (core 0, ~1 s), Wi-Fi, signed HTTP
  include/config.example.h   copy to config.h (git-ignored): Wi-Fi, backend host, device id/key, pins
  test/host_tests.cpp  PC test driver used by backend/tests/test_firmware_core.py
```

## Build and flash (needs PlatformIO)
```
cd firmware/intersection
copy include\config.example.h include\config.h      # edit it
pio run -e lamptest -t upload                       # bring-up: one LED at a time, no network
pio run -e esp32dev -t upload ; pio device monitor
```

## Tests (no hardware, no ESP32 toolchain)
`pip install -r requirements-dev.txt` (a small C++ compiler wheel), then `pytest backend/tests/test_firmware_core.py`:
compiles the firmware core with `-Wall -Wextra -Werror` and checks it against the Python implementation —
golden FSM traces (including 32-bit `millis()` wrap), signatures for every SHA-256 padding edge case, parsing of the real
`/api/device/commands` payload, state/ack bodies accepted by the real endpoints, and the 595 bit-serial chain. Deliberately
breaking the FSM, the HMAC or the shift order makes these tests fail.

## What is and isn't verified
* **Verified on a PC:** everything in `lib/flowsync_core`.
* **Not compiled or run:** `src/main.cpp` (needs the ESP32 toolchain — see the PlatformIO note above), the watchdog behaviour, Wi-Fi/HTTP timing, and the electrical design.
* **Not implemented:** intersection ultrasonic sensors (Tier 2), OTA updates, ESP-NOW (dropped by design: the backend is the control authority).
