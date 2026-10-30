# Hardware build and rehearsal runbook

Two separate demos, not linked to each other while testing:

* **Software demo** (laptop, phones, backend, SUMO screen): shows how FlowSync works. Runbook: `docs/DEMO_SCRIPT.md`.
* **Hardware demo** (tabletop + car + camera): three **standalone** prototypes that show how it would look in real life. None of them
  needs Wi-Fi, the backend or the laptop, so the hotspot cannot break them.

Work through the gates in order. Every gate has a pass criterion you can check by eye or on the serial monitor (115200 baud,
`pio device monitor -d <project>`). Wiring: `HARDWARE_WIRING.md` (table), `HARDWARE_CAR_WIRING.md` (car). Everything below compiles
today; **nothing has been run on a board yet**, so each gate is also the first real test.

| Person | Owns |
|---|---|
| **A: hardware** | wiring, flashing, table build |
| **B: software/demo** | backend, scenarios, dashboards, LLM key, rehearsal, pitch |

## Parts
Tabletop: 2 ESP32 DevKits, 6 traffic modules (GND, R, Y, G), breadboard, jumpers, USB power. Car: ESP32 DevKit, MPU-9250, 16x2 I2C LCD,
HC-SR04, 1 kohm + 2 kohm resistors, push button, passive buzzer. Camera: ESP32-CAM with its MB board. **Buy spare jumpers and a spare ESP32.**
The SN74LS95N chips are not usable (no latch, no output enable) and are not part of the build.

## Gate 1: table lamps (A, 45 min)
`pio run -d firmware/table -e board1_test -t upload`, then the same with `board2_test` on the second DevKit.
In the monitor type `m` (pin map) and `l` (walk).
**Pass:** each module lights one lamp per second in the order red, yellow, green; the printed name (for example `I2-A GREEN (D33)`) matches the module you are looking at;
`r`, `y`, `g` light all lamps that colour; `o` puts everything red. **Fix** a wrong colour by swapping that module's wires.

## Gate 2: table demo (A, 30 min)
Flash `board1` and `board2` (`pio run -d firmware/table -e board1 -t upload`, same for `board2`). Power both from the same supply so their loops start together.
**Pass:** every intersection alternates A / B, always through yellow and a 1 s all-red, never two directions lit at once. At 36 s into each 50 s loop (or when you press the
BOOT button on both boards) I1 (B) and I2 (A) go green for the corridor for 14 s while I3 keeps cycling; then the normal cycle resumes.
**Fallback:** if a board misbehaves, run the other board alone and show the other intersections on the software demo.

## Gate 3: car sensors (A, 60 min)
Wire in the order in `HARDWARE_CAR_WIRING.md` section 6 and flash `cartest`.
**Pass:** the scan shows the MPU at 0x68 and the LCD at 0x27 or 0x3F; `dist` follows a hand in front of the sensor; the button prints `PRESSED`; `b` beeps and `z` sweeps four clearly different tones;
`l` writes text on the LCD. If the buzzer only clicks it is active: rebuild with `-DBUZZER_PASSIVE=0`.
**If the upload fails** with "Failed to communicate with the flash chip": flash the bare board with nothing wired to GPIO 0, 2, 12, 15, change the cable or port, hold BOOT while it connects,
or add 10 uF between EN and GND.

## Gate 4: car run mode (A, 30 min)
Flash `car` (`pio run -d firmware/car -e car -t upload`).
**Pass:** a hand 30 cm in front gives beeps and `Obstacle 30cm` on the LCD, closer is faster; tapping or tilting hard shows `ROAD HAZARD`; a firm shake shows `CRASH DETECTED`; the button shows `EMERGENCY ALERT`
and the siren, and the second line says `demo: not sent`. **Tune** `HAZARD_DEV_G` and `CRASH_G` in `firmware/car/platformio.ini` if a toy car is too jumpy or too stiff.

## Gate 5: ESP32-CAM (A, 30 min)
Flash `camtest` then `camweb` (`pio run -d firmware/camtest -e camweb -t upload`; GPIO0 to GND while flashing on a bare board, not needed on the MB board). Power it from a solid 5 V, 2 A.
**Pass:** the monitor prints the sensor `PID=0x26`; a phone joined to the Wi-Fi `FlowSync-CAM` (password `flowsync1`) loads `http://192.168.4.1/` and "Take photo" shows a fresh picture.
Save a few photos of a dark "pothole" print on light paper; the laptop software demo uses saved photos for the pothole detector.

## Gate 6: rehearse (B leads, 3 x 25 min)
Run both demos end to end three times, each with a different presenter: the software demo from `docs/DEMO_SCRIPT.md` (phones joined by QR), and the hardware demo
(table cycle and BOOT-button corridor, car alerts, camera photo). Record what failed and what you changed.
**Pass:** two consecutive runs without manual intervention.

## Gate 7: demo freeze (at least 2 h before judging)
No new features. Change development passwords and keys if the dashboard is reachable by others (`.env`, `config/devices.yaml`); pre-load the browser tabs;
charge the power banks; pack spares; one full run on the actual table.

## Always-ready fallback
| If this fails | Do this | What the audience is told |
|---|---|---|
| A table board | run the other board; show the rest in the software demo | "one of two controllers" |
| Car LCD | the buzzer and the serial monitor still show the alerts | "display not connected" |
| ESP32-CAM | show saved photos | "recorded photos" |
| Wi-Fi / hotspot | nothing: the hardware prototypes need none; software demo falls back to the replay tab | "recording of a previous run" |

## Risk register
| Risk | Likelihood | Mitigation |
|---|---|---|
| ESP32-CAM brown-out | medium | 5 V, 2 A, 470-1000 uF capacitor, short cable |
| Boards start out of step so corridors do not line up | medium | power both from one switch; press BOOT on both together |
| Toy-car thresholds too sensitive | medium | tune `HAZARD_DEV_G` and `CRASH_G` at gate 4 |
| Upload errors on a DevKit | medium | different cable/port, hold BOOT, bare board first |
