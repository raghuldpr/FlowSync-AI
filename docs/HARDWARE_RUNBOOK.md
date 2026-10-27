# Hardware build and rehearsal runbook

For two people. Software is complete and tested on the simulator; **what remains is making the physical table real.**
Work through the gates in order; do not start a gate until the previous one passes. Every gate has a pass criterion you can check
with a command or by eye. If a gate fails and is not fixed in its time box, use the fallback written next to it — the demo
must always be runnable (see "Always-ready fallback" at the end).

| Person | Owns |
|---|---|
| **A — hardware** | wiring, firmware flashing, camera mount, markers, table build |
| **B — software/demo** | backend, preflight, scenarios, dashboards, LLM key, rehearsal script, pitch |

## Before you start (shopping — see `HARDWARE_SPEC_v2.md` for the full BOM)
Essential: ESP32 DevKit, 3× 74HCT595, 18 LEDs + resistors, 10 kΩ, capacitors, breadboard, jumpers, USB power bank + hub + cables, phone + rigid
overhead mount, printed ArUco markers (5), micro toy cars, board/paint, 2.4 GHz router. Optional: ESP32-CAM + USB-serial adapter (road camera),
second ESP32 (car electronics). **Buy spare LEDs, a spare ESP32 and spare jumpers** — they are the usual casualties.

## Gate 0 — laptop only (B, 20 min)
```
.\.venv\Scripts\python -m uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000   (PYTHONPATH=backend)
.\.venv\Scripts\python -m simulator.run_sim
.\.venv\Scripts\python -m app.preflight --drill drill_llm_down
```
**Pass:** preflight prints `READY`. Windows firewall: allow inbound TCP 8000 on the private network. Put the LAN address it prints into
the firmware `config.h` files and keep the laptop on a DHCP reservation.
**Also today:** put the LLM key in `.env`, ask one question in the Assistant panel, and decide whether the venue demo uses it (needs internet) or the labelled fallback.

## Gate 1 — LED chain (A, 60 min)
Build one 74HCT595 and 6 LEDs first, then the other two. Flash the bring-up image:
`cd firmware\intersection ; copy include\config.example.h include\config.h ; pio run -e lamptest -t upload`.
**Pass:** LEDs light **one at a time**, in the order `I1 A_R A_Y A_G B_R B_Y B_G`, then I2, then I3; all 18 work; brightness is even; power-cycling with no
firmware leaves every LED **off**.
**Fallback:** if a chip misbehaves, swap it (spare) and re-check pin 10 (SRCLR → 5 V) and pin 13 (OE pull-up). Wiring and values: `HARDWARE_WIRING.md`.

## Gate 2 — controller firmware (A, 45 min)
Edit `include/config.h` (Wi-Fi, laptop address, `DEVICE_KEY` = `intersection-1` key in `config/devices.yaml`). Replace the dev keys first
(`devices.yaml`) if the demo is not private. `pio run -e esp32dev -t upload ; pio device monitor`.
**Pass (backend OFF):** boot shows all-red, after 5 s each intersection alternates A/B every 8 s, **always through yellow and a 1 s all-red**.
**Pass (backend ON):** `preflight` shows `device intersection-1 … ok`; the dashboard's "Intersection controller" row says **Built**; the lamps on the table
match the dashboard. Pull the router's power: lamps keep cycling safely; restore it: control resumes. Run **Drill → controller offline** and read its checks.
**Fallback:** none needed for safety (the firmware falls back by itself). If Wi-Fi is hopeless, run the simulator for the controller and keep the table as a prop.

## Gate 3 — table and markers (A, 90 min, can run in parallel with gate 2)
1. Draw the table in the **layout frame** of `config/topology.yaml` (`docs/VISION_SETUP.md` §1): lanes 28 units apart, round intersection areas of radius 30.
2. `python -m app.vision.markers --out print_markers --mm-per-unit <your scale>`; print at 100 %; measure the black square against `sizes.txt`.
3. Stick corner markers 10–13 at the layout corners, flat, upright. Marker 20 on the roof of the connected car, **top = the car's nose**.
4. `python -m app.vision.capacity` and set the physical capacities (`--write config/topology.physical.yaml`, then `FLOWSYNC_TOPOLOGY_PATH`). If a lane holds < 2 cars, enlarge the table or use smaller cars.
**Pass:** a ruler check — marker centres within 3 mm of the layout coordinates; lanes drawn on both sides of every road.

## Gate 4 — camera calibration and counting (A+B, 90 min)
Mount the phone rigidly; **lock exposure, focus and white balance**; start an MJPEG/RTSP stream and open the URL in a browser first.
`python -m app.vision.run --source <url> --capture-reference 10` with the table **empty** (corner markers visible).
**Pass:** it prints the calibration error (target < 3 layout units). Then run `--debug-dir debug` and do the **spot check**: place 0, 1, 2, … cars on each approach,
wait 3 s, record the debug image count, and fill this table (keep it in `docs/RESULTS_PHYSICAL.md`):

| lane | cars placed | counted | ok? |
|---|---|---|---|
| … | … | … | … |

Target: exact on ≥ 90 % of placements. **If not:** tune `diff_threshold` (lower for dim/low-contrast cars), `car_length/width`, add contrast (dark cars on a light road),
re-capture the reference. **Fallback:** recorded video (`--source file.mp4`, labelled `recorded_video`) or the synthetic camera (`run_sim --vision`).

## Gate 5 — emergency vehicle tracking (A, 45 min)
With the stream running, declare an EV on the dashboard and push the car slowly along W1 → L13 → O3.
**Pass:** the red EV marker follows the car on the map within ~0.5 s, the road/edge is right in both directions (lane-aware), it stays on the previous road while inside an
intersection, and covering the marker for 5 s shows "location stale" and withdraws warnings (**Drill → ambulance location lost**).
**Fallback:** declare/drive the EV in the simulator and keep the physical car as a prop (the provenance table will say so).

## Gate 6 — road camera (optional, A, 45 min)
Flash `firmware/roadcam` (`pio run -e esp32cam -t upload`; GPIO0 → GND while flashing). Power it from a solid 5 V with a 470–1000 µF capacitor.
Print a dark ellipse "pothole" on light paper, lay it on the road. **Pass:** the dashboard shows an **unreviewed** hazard with a camera frame; **Verify** it and watch
the route penalty and the alert on a road user's phone. **Fallback:** declare the hazard from the dashboard (operator report) — same downstream behaviour.

## Gate 7 — rehearsals (B leads, 3 × 25 min)
Run `docs/DEMO_SCRIPT.md` end to end in **physical mode** three times, each time with a different person presenting, phones joined at `/driver.html`.
Record: which checks failed, why, what you changed. Run `python -m simulator.evaluate --seeds 8 --city` once and keep `docs/RESULTS.md` current.
**Pass:** two consecutive runs with every self-check ✓ and no manual intervention.

## Gate 8 — demo freeze (≥ 2 h before judging)
No new features. Final `app.preflight --drill drill_llm_down` → READY; change all development passwords/keys if the dashboard is reachable by others
(`.env`, `config/devices.yaml`); clear stale data (restart backend); pre-load the browser tabs (operator, presenter, one phone); charge power banks;
pack spares; one full run on the actual table.

## Always-ready fallback (decide this now, not on stage)
| If this fails | Do this | What the audience is told (the dashboard already says it) |
|---|---|---|
| Wi-Fi / router | run backend + simulator on the laptop, table as a prop | "simulated controller" |
| Camera | `--source recording.mp4` or `run_sim --vision` | "recorded / synthetic camera" |
| LED chain | simulator for the controller | "simulated controller" |
| LLM / internet | nothing: labelled deterministic fallback | "LLM unavailable (drill shows this)" |
| Everything | **Replay tab** → replay the last good recorded run | "recording of a previous run" |

## Risk register
| Risk | Likelihood | Mitigation |
|---|---|---|
| Venue Wi-Fi blocks devices | high | own router, 2.4 GHz WPA2, fixed channel, DHCP reservation, never venue Wi-Fi |
| Lighting changes break counting | high | locked exposure, fresh reference, lights fixed, drill + fallback |
| ESP32-CAM brown-out | medium | 5 V ≥ 2 A, 470–1000 µF cap, short cable |
| Laptop sleeps / firewall prompt | medium | disable sleep, pre-approve firewall rule |
| Presenter nerves | medium | presenter view + scripted narration + self-checking steps |
