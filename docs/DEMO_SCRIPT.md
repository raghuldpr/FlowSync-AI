# Demo script — "Ambulance through congestion"

One continuous scenario (about 2¾ minutes) from a changing traffic condition to a useful system response, including an
ambulance approaching congestion with warnings that change when its route changes. The scenario engine runs it, narrates it,
and **checks itself**: every claim below shows a ✓/✗ with evidence on the dashboard and on the presenter screen.

## Pre-flight (10 minutes before)
```
python -m app.preflight --url http://LAPTOP_IP:8000 --drill drill_llm_down
```
It must end with `READY` (WARN lines — dev passwords, no LLM key — are reminders, not failures). It also prints the laptop's LAN address: put it in the firmware
`config.h` files and give phones `http://LAPTOP_IP:8000/driver.html`. Then: operator dashboard on the laptop, **presenter view on the second screen**, one phone joined as a driver.
Network: follow `docs/NETWORK_SETUP.md` (Windows hotspot on 2.4 GHz, firewall rule for port 8000), then run `python -m app.joinqr` to print the join links and write the printable QR sheet (`data/qr/join.html`). Windows Mobile Hotspot is believed to allow about 8 clients: count devices; bring a router as a backup.
The hardware prototypes (table, car, camera) are standalone and run separately from this script: see `docs/HARDWARE_RUNBOOK.md`.

## Run modes
| Mode | Start | World |
|---|---|---|
| Fully simulated (laptop only) | backend + `python -m simulator.run_sim` → Scenario panel → *Start* | the simulator obeys the scenario's world commands |
| Simulated + synthetic camera | `python -m simulator.run_sim --vision` (backend on the physical topology) | queues and EV are rendered, then **measured** by the real vision pipeline |
| Backend-connected physical table (future work) | backend + a flashed `firmware/intersection` controller (the old one-ESP32 design) | nobody polls the simulator channel → the panel shows **▶ On the table:** cues; a person performs them. Not part of the current hardware demo, whose table is standalone |
| City view (12 intersections, simulated) | backend + simulator with `FLOWSYNC_TOPOLOGY_PATH=config/topology.grid.yaml` (+ devices.grid.yaml) → scenario `city_demo` | generated grid, same controller code |

The same script drives all modes. In physical mode, checks tied to a cue only pass if the cue is performed.

## The script
| t (s) | Presenter says | Audience sees | Problem-statement item | Self-check |
|---|---|---|---|---|
| 0 | "Three intersections, one connected system. The lamps you see are what the controller *reports*, not what we commanded." | map, lamps in sync | monitoring of queues and phases | — |
| 12 | "A queue builds on the direct West→South link." | L13 turns red, ▲ appears | monitoring, incident detection | congestion incident on L13 |
| 34 | "The operator is alerted with an impact assessment." | Alerts badge; incident card: affected intersections, delay, alternative path | impact assessment, alternative routes, operator notification | impact lists an alternative |
| 42 | "The operator asks the assistant." | answer citing tool calls (or the labelled deterministic fallback) | LLM as reasoning and tool-orchestration layer | assistant answered |
| 50 | "Other road users join — each gets a private feed." | `road users: 2` chip; (phones: join at `/driver.html`) | warnings shown only to **relevant** road users | only the car on the ambulance's corridor is warned |
| 55 | "An ambulance is dispatched West→South." | route drawn via Intersection 2; warning bubbles only on approaches it will reach; the corridor starts clearing | location-aware warnings; adaptive signals | route avoids L13 |
| 60 | "A breakdown closes L12 — the route changes." | dashed closure; route flips to L13; old warning **cancelled**, new one **redirected**; phones get "route changed" | warnings update / redirect / cancel | L12 cancelled + L13 redirected |
| 75 | "The intersections clear the way — through the same safety machine." | I1 / I3 "holding green for the emergency vehicle"; yellow and all-red never skipped | deterministic, validated signal changes | priority at I1 and I3 |
| 112 | "The ambulance arrives; priority is released." | all warnings expired, no unsafe lamp state, road users told "has passed" | warnings cancel when the vehicle passes | arrived, 0 live warnings, 0 unsafe |
| 124 | "Recovery." | queues drain, incidents clear | resilience | all incidents cleared |

After the run the presenter screen shows tiles (ambulance trip time, reroutes, priority decisions, incidents, road users warned / not warned).
The trip time in the tiles is a measurement of one scripted run, **not** a benefit claim; benefits come from the paired comparisons in `docs/RESULTS.md`.

## Judges join (about 2 minutes, the part to rehearse most)
All V2V below is **simulated** in the backend and says so on screen.
1. A judge scans a gate QR code (or `/driver.html`), picks **vehicle** or **ambulance**, and gets a car with a colour and plate. No name, no account.
2. **Vehicle:** drives a virtual car along a random trip. When an ambulance comes up behind them on their route they get a full-screen takeover with the fixed wording and an ETA band; a car on another road gets nothing. When the route changes they are told it no longer approaches; later "has passed".
3. **Ambulance:** picks a start and a destination, starts the trip (signal priority and rerouting as in the scripted run) and can press *Raise emergency alert*, a simulated V2V broadcast that reaches only vehicles on its route ahead. The alert has a 15 s cooldown and only the ambulance role can send it.
4. At arrival each phone shows a trip receipt and the session is deleted. The presenter screen's guest panel shows who was warned and who was not.
The same flow is available as a feed for the big screen (`docs/GUEST_API.md`).

## Optional extras (pick what time allows)
1. **Phone, live (60 s):** an audience member opens `/driver.html`, picks an entrance and a destination, drives a virtual car. During the ambulance run only they, if on its corridor, get the emergency alert; a car on another road gets nothing.
2. **Road condition (60 s):** from the phone press *Report a hazard* (or declare one in the dashboard's Road conditions panel) → it appears as **unreviewed** → the operator **verifies** it → the route cost rises and road users heading there are warned. A public report alone changes nothing.
3. **Assistant proposal (45 s):** type "Please give I2 axis B a green for 15 seconds" → a proposal appears → the validator's verdict → **Confirm** → the intersection still goes yellow → all-red → green.
4. **Replay (30 s):** *Replay* tab → pick the last run → play at 4× (also the fallback if the live run misbehaves).
5. **City (60 s):** switch to the 12-intersection city scenario (simulated) to show the same code at scale.
6. **Results (30 s):** the paired A/B tables in `docs/RESULTS.md` (simulated), stated with their spread.

## Failure drills (show one live; all four are in the Scenario list)
| Drill | What it proves | Expected |
|---|---|---|
| Camera feed lost | stale data is never treated as zero | readings flagged stale after 5 s, controller holds (`no_fresh_data_hold`), recovers on return |
| Ambulance location lost | stale location withdraws warnings and priority | after 4 s: warnings → `cancelled_stale`, priority gone; tracking resumes when the marker is seen |
| LLM provider unavailable | the LLM is never required for safety | answers switch to the labelled deterministic fallback; control unaffected |
| Intersection controller offline | safe local fallback | device runs a fixed-time cycle (always yellow + all-red); dashboard shows stale reports; public view says "unknown" |

## If something goes wrong on stage
* **A check shows ✗:** say what it means (the detail line states exactly what was not observed), carry on; the checks are the point.
* **Scenario stuck / odd world state:** *Stop* (cleans up the world, closes the session, releases closures) then *Start* again.
* **Backend restarted:** log in again; EV sessions end as `backend_restart`; re-run.
* **No LLM / no internet:** nothing to do — it is already handled and labelled.
* **Hardware misbehaves:** switch the world to the simulator (start `run_sim`); the provenance table updates itself, so the "built vs simulated" statement stays true.
* **Anything else:** **Replay** tab → replay the last good recorded run and say so.

## Claims we can and cannot make
| Can say (verified) | Cannot say (not yet) |
|---|---|
| Every signal command passes a deterministic validator; min green, yellow, all-red are always enforced | that the ESP32 table, car or camera work on hardware (they compile and their logic passes host tests; not yet flashed or bench-tested) |
| Warnings use fixed, lawful wording; phones and public views never expose emergency-vehicle ids, routes or positions; relevance is decided on the server | that vision counts are accurate on a real feed (verified only on a synthetic camera; the table has no camera) |
| All restricted actions **and denials** are audit-logged; viewer / operator / admin enforced on the backend | that the LLM improves outcomes (it explains and proposes; humans confirm; control never depends on it) |
| Stale data is shown as stale, never as zero; a starvation bug found by our own evaluation was fixed and is now regression-tested | any benefit figure for a real road — the numbers in `docs/RESULTS.md` come from our simulator's models and are labelled as such |
| Measured (simulated) results come with seeds, spread and paired comparisons — including the cases where a feature does **not** help | that rerouting helps around a moderate jam (it does not in our model; it helps around a blocked road) |

## Likely judge questions
* *"What stops the LLM from changing signals?"* — It has no write access. It can only *propose*; the proposal is checked by the same validator, a human must confirm, the validator re-checks, and the controller still enforces clearances. Emergency priority outranks it.
* *"How do you decide who is warned?"* — On the server: the emergency vehicle must come up *behind* the user within a 20 s window on a road they are on or about to enter. The other direction, other roads, and cars already ahead of the ambulance get nothing. It is a tested rule, shown live by the check at t = 50.
* *"What do road users see / what do you store?"* — Fixed template text plus a coarse ETA band. Road-user sessions are random tokens in memory, deleted at arrival. Raw telemetry is purged on a schedule (24 h traffic, 6 h EV positions); the audit log is kept.
* *"What if the network or the backend dies?"* — The firmware falls back to a fixed-time cycle after 5 s without a successful exchange; stale readings are flagged, not trusted (drills).
* *"What is real and what is simulated?"* — Open the *Built vs simulated* tab: it is generated from what each data path reports about itself.
* *"How do you know it works?"* — 350+ automated tests, a self-checking scenario that passes on every random seed we tried, paired A/B experiments with their spread, and our own bug found that way (an ambulance starving at a stop line).
