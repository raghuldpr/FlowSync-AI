# What is built and what is simulated

The brief asks the demo to say this clearly. The dashboard's **Built vs simulated** tab (and the presenter screen) states it
live: each data path labels itself — the controller reports `origin` (`hardware` / `simulated`), the camera reports a traffic
`source` (`overhead_camera`, `recorded_video`, `simulated`), the EV position carries `camera` / `simulated`. The table below is the
static version of the same statement.

| Component | In software (always real code) | Physical tabletop | Simulation |
|---|---|---|---|
| Signal decision logic: pressure control, spillback and gridlock rules, validator, min green / yellow / all-red | **Built** — identical in every mode | same code | same code |
| Intersection controller (3 intersections, 18 lamps) | firmware core is ported and tested against the reference | ESP32 + 3× 74HCT595 + LEDs — **firmware compiles (esp32dev, lamptest); not yet flashed or wired** | virtual tabletop runs the *same* state machine |
| Queue counts and "stationary" time | vision pipeline (ArUco calibration, background differencing) — verified on a synthetic camera | overhead phone camera — **not yet run on a real feed** | simulator counts, or a synthetic camera whose images go through the real pipeline |
| Emergency-vehicle position | ArUco tracking → road + progress | toy car with roof marker — **not yet tested** | scripted driver following the system's current route |
| Routing, rerouting, warnings (update / redirect / cancel / stale) | **Built** | same | same |
| Incident detection, impact assessment, alerts | **Built** (congestion, stalled vehicle) | triggered by real queue behaviour | triggered by scripted world events |
| Roles, audit log, public redaction | **Built** | same | same |
| LLM reasoning layer | **Built**; verified against a stand-in model only; labelled fallback | needs internet + key | same |
| Road users (phones): sessions, relevance, alerts, route suggestions | **Built** (server-side relevance, fixed wording, privacy-minimal) | judges' phones join at `/driver.html` | each phone user drives a **virtual** car (dead reckoning); no GPS |
| Road conditions: hazard workflow, route penalty, alerts, image upload | **Built** | ESP32-CAM firmware compiles; **not flashed**; operator reports work without it | scripted/operator-declared hazards |
| Pothole finder | classical-vision **heuristic** (dark patch), verified on synthetic images only; every detection needs operator verification | printed dark prop on the road | — |
| City-scale network (12 intersections) | **Built** (generator + the same controller) | — | **Simulated** (`topology.grid.yaml`) |
| Measured A/B results (`docs/RESULTS.md`) | harness is real | — | numbers come from the **simulator's models** |

## What "simulated" means here
* The simulator replaces **physics** (cars arriving, queues draining, a car driving along its route) and the **device side** of the
  protocol. Everything the backend does with that input is the real code path, using the same signed device API.
* Scenario "world" commands (a jam, a breakdown, a dead camera) exist only to script the demo; on the physical table a person
  performs the equivalent cue shown on screen.
* The EV's *route choices* are the system's; the simulated driver simply follows them (a real driver would read the dashboard).
