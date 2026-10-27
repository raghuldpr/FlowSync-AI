# Firmware

`intersection/` — ESP32 controller for all three tabletop intersections (Phase 6). Wiring: [docs/HARDWARE_WIRING.md](../docs/HARDWARE_WIRING.md).

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
