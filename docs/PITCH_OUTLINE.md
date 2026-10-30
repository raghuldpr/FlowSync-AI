# Pitch outline (about 5 minutes + demo)

Principle: say plainly what is built, what is simulated and what is untested. It is the strongest trust signal we have. Update the "on the table" claims after the hardware is tested; until then say "simulated" or "compiled, not yet run on a board".

## Flow

| Time | Beat | Say | Show |
|---|---|---|---|
| 0:00 | Problem | Signals are blind to emergencies and incidents; drivers learn about congestion too late; alerts reach everyone or nobody. | One slide |
| 0:30 | The idea | One connected system: the backend decides, devices carry it out, and a safety validator is always in the loop. The LLM reasons and proposes but never controls. | Architecture slide |
| 1:00 | Live demo | Judges scan a QR code and join as a vehicle or an ambulance driver. The ambulance driver gets priority and can raise a **simulated** V2V alert; only the relevant vehicles are warned. | Phones, big screen, presenter screen (`docs/DEMO_SCRIPT.md`) |
| 2:30 | In real life | "This is how it would look in a real vehicle and junction." Three standalone prototypes: tabletop signals, the car brain (obstacle, bump, emergency button) and the ESP32-CAM. They are not connected to the software demo. | Table, car, camera |
| 3:30 | Evidence | Measured on our simulator (8 seeds) and in SUMO (5 seeds). Show wins and non-wins. | Results slide, SUMO view |
| 4:30 | Honesty | Built / simulated / untested table. | `docs/BUILT_VS_SIMULATED.md` or the live dashboard tab |
| 5:00 | Close | Next: real GPS, V2X standards, a real intersection pilot. | One slide |

## Problem statement to evidence

| Requirement | Where it is shown | Evidence |
|---|---|---|
| Monitor queues, phases, road conditions, incidents | Operator and presenter views | Controller reports phases; vision counts queues (synthetic camera verified so far; the table has no camera) |
| Adaptive timing inside deterministic safety rules | Signals during the demo | Min green, yellow, all-red, spillback and starvation rules; validator. Delay down 35 % ± 9 (balanced) and 35 % ± 13 (one heavy entrance) in simulation |
| Incident detection, impact, alternative routes, operator alert | Demo step at 12 to 34 s | Impact assessment lists affected intersections and an alternative path |
| Location-aware EV warnings, only for relevant users | Warning bubbles and phones | Relevance decided on the server; only corridor phones alert |
| Warnings update, redirect, cancel | Demo step at 60 s and arrival | Self-check: old warning cancelled, new one redirected; "has passed" |
| Public vs operator access, enforced and logged | Operator audit tab | Roles checked on the backend; denials logged |
| LLM reasons over tools, never controls | Assistant panel | Read tools plus one proposal that is validated, confirmed by an operator and validated again; deterministic fallback |
| One full scenario, built vs simulated stated | Presenter self-checks | Live built-vs-simulated table |

## Numbers to quote (simulated; our simulator 8 seeds, SUMO 5 seeds, mean ± sd)

- Ambulance trip with a stalled vehicle on the direct road: 95.6 to 56.0 s, 41 % ± 8 saving (rerouting does most of it).
- 12-intersection city with a jam on the route: 114.0 to 79.1 s, 29 % ± 10.
- Adaptive vs fixed-time delay: 35 % reduction on balanced demand and on one heavy entrance.

SUMO (SUMO's driver model with our real controller, `docs/RESULTS_SUMO.md`):

- Waiting time per vehicle about halves against fixed-time signals: 3.6 s vs 7.7 s (balanced demand) and 4.1 s vs 7.8 s (rush from the West).
- The ambulance with FlowSync never stops (0 s vs 8 s stopped), but its **trip is not shorter**: 71.2 ± 7.0 s vs 69.4 ± 1.3 s (the baseline overtakes the jam in the second lane; FlowSync takes a longer detour). Say this before a judge asks.

Do not quote these as real-road results.

## Questions to prepare for

- *Is this real or simulated?* Answer with the built/simulated table, not adjectives.
- *Where is it weak?* Adaptive signals show no gain with three heavy entrances (1 % ± 14) or on the balanced city grid (6 % ± 8); rerouting around a moderate jam did not help (-10 % ± 15).
- *Why no GPS?* Judges' phones drive a virtual car (dead reckoning) and the standalone prototypes have no GPS; a GPS module is the production path.
- *Can the LLM change signals?* No. It can propose one change; the validator and an operator confirmation gate it, and emergency priority outranks it.
- *What about privacy?* Anonymous in-memory road-user sessions, fixed-template warnings that never include the vehicle's identity, route or position, scheduled purge of raw telemetry.
- *Isn't a phone while driving unsafe?* The driver view is a glanceable, no-typing display; in the demo it stands in for an in-vehicle unit.
- *V2V?* Simulated in the backend, not a radio link: a guest ambulance's alert goes to the backend, which decides who is relevant. Only the ambulance role can send it, it is rate-limited, and the wording is fixed. The message types map to SAE J2735 (`docs/V2V_MESSAGES.md`); real DSRC / C-V2X is future work.
- *Does the hardware talk to the software?* No. The tabletop, car and camera are standalone prototypes that show how it would look in real life; the software demo shows how it works. We kept them separate on purpose.
- *Pothole detection?* A pretrained model, not yet measured on our camera; until its weights are installed the demo uses an operator-declared hazard. Every detection needs operator verification before drivers see anything.
