# What is built and what is simulated

The brief asks the demo to say this clearly. The dashboard's **Built vs simulated** tab (and the presenter screen) states it
live: each data path labels itself — the controller reports `origin` (`hardware` / `simulated`), the camera reports a traffic
`source` (`overhead_camera`, `recorded_video`, `simulated`), the EV position carries `camera` / `simulated`. The table below is the
static version of the same statement.

| Component | In software (always real code) | Physical tabletop | Simulation |
|---|---|---|---|
| Signal decision logic: pressure control, spillback and gridlock rules, validator, min green / yellow / all-red | **Built** — identical in every mode | same code | same code |
| Intersection controller (3 intersections) | firmware core is ported and tested against the reference | **standalone** tabletop: 2 ESP32 DevKits light 6 traffic modules directly from pins, built-in cycle + scripted ambulance corridor, **no backend link** — firmware compiles and its logic is host-tested; **not yet flashed or wired** | virtual tabletop runs the *same* state machine |
| Queue counts and "stationary" time | vision pipeline (ArUco calibration, background differencing) — verified on a synthetic camera | not on the table (no overhead camera); shown by screen-recording `run_sim --demo --vision` | simulator counts, or a synthetic camera whose images go through the real pipeline |
| Emergency-vehicle position | ArUco tracking → road + progress | not on the table (no toy car or marker) | scripted driver following the system's current route |
| Connected car (sensors, alerts) | alert logic host-tested | **standalone** car brain: HC-SR04 obstacle beeps, MPU-9250 bump/crash alerts, button "emergency alert" shown on an LCD and a buzzer; **local only, not transmitted** — compiles; **not yet flashed or bench-tested** | the V2V broadcast to other vehicles is simulated in the backend |
| Routing, rerouting, warnings (update / redirect / cancel / stale) | **Built** | same | same |
| Incident detection, impact assessment, alerts | **Built** (congestion, stalled vehicle) | triggered by real queue behaviour | triggered by scripted world events |
| Roles, audit log, public redaction | **Built** | same | same |
| LLM reasoning layer | **Built**; verified against a stand-in model only; labelled fallback | needs internet + key | same |
| Road users (phones): sessions, relevance, alerts, route suggestions | **Built** (server-side relevance, fixed wording, privacy-minimal) | judges' phones join at `/driver.html` | each phone user drives a **virtual** car (dead reckoning); no GPS |
| Road conditions: hazard workflow, route penalty, alerts, image upload | **Built** | standalone ESP32-CAM web camera (`camweb`, own Wi-Fi, takes photos; not linked to the software) compiles, **not flashed**; the backend-connected `roadcam` firmware also compiles, **not flashed**; operator reports work without a camera | scripted/operator-declared hazards |
| Pothole finder | classical-vision **heuristic** (dark patch), verified on synthetic images only; every detection needs operator verification | printed dark prop on the road | — |
| City-scale network (12 intersections) | **Built** (generator + the same controller) | — | **Simulated** (`topology.grid.yaml`) |
| Measured A/B results (`docs/RESULTS.md`) | harness is real | — | numbers come from the **simulator's models** |
| SUMO road-traffic view (`sumosim/`) | **Built** — bridge, network generated from `topology.yaml`, ambulance + scenario ops, tests | — | **Simulated**: SUMO's driver model supplies vehicles; our real controller sets the lights through the same signed device API (`docs/RESULTS_SUMO.md`) |

## What "simulated" means here
* The simulator replaces **physics** (cars arriving, queues draining, a car driving along its route) and the **device side** of the
  protocol. Everything the backend does with that input is the real code path, using the same signed device API.
* Scenario "world" commands (a jam, a breakdown, a dead camera) exist only to script the demo; on the physical table a person
  performs the equivalent cue shown on screen.
* The EV's *route choices* are the system's; the simulated driver simply follows them (a real driver would read the dashboard).
