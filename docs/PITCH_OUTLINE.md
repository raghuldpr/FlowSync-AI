# Pitch outline (about 5 minutes + demo)

Principle: say plainly what is built, what is simulated and what is untested. It is the strongest trust signal we have. Update the "on the table" claims after the hardware is tested; until then say "simulated".

## Flow

| Time | Beat | Say | Show |
|---|---|---|---|
| 0:00 | Problem | Signals are blind to emergencies and incidents; drivers learn about congestion too late; alerts reach everyone or nobody. | One slide |
| 0:30 | The idea | One connected system: the backend decides, devices carry it out, and a safety validator is always in the loop. The LLM reasons and proposes but never controls. | Architecture slide |
| 1:00 | Live demo | "Ambulance through congestion", then judges join by QR. | Table, presenter screen, phones (`docs/DEMO_SCRIPT.md`) |
| 3:30 | Evidence | Measured on our simulator, 8 seeds. Show wins and non-wins. | Results slide |
| 4:30 | Honesty | Built / simulated / untested table. | `docs/BUILT_VS_SIMULATED.md` or the live dashboard tab |
| 5:00 | Close | Next: real GPS, V2X standards, a real intersection pilot. | One slide |

## Problem statement to evidence

| Requirement | Where it is shown | Evidence |
|---|---|---|
| Monitor queues, phases, road conditions, incidents | Operator and presenter views | Controller reports phases; vision counts queues (synthetic camera verified so far) |
| Adaptive timing inside deterministic safety rules | Signals during the demo | Min green, yellow, all-red, spillback and starvation rules; validator. Delay down 35 % ± 9 (balanced) and 35 % ± 13 (one heavy entrance) in simulation |
| Incident detection, impact, alternative routes, operator alert | Demo step at 12 to 34 s | Impact assessment lists affected intersections and an alternative path |
| Location-aware EV warnings, only for relevant users | Warning bubbles and phones | Relevance decided on the server; only corridor phones alert |
| Warnings update, redirect, cancel | Demo step at 60 s and arrival | Self-check: old warning cancelled, new one redirected; "has passed" |
| Public vs operator access, enforced and logged | Operator audit tab | Roles checked on the backend; denials logged |
| LLM reasons over tools, never controls | Assistant panel | Read tools plus one proposal that is validated, confirmed by an operator and validated again; deterministic fallback |
| One full scenario, built vs simulated stated | Presenter self-checks | Live built-vs-simulated table |

## Numbers to quote (simulated, 8 seeds, mean ± sd)

- Ambulance trip with a stalled vehicle on the direct road: 95.6 to 56.0 s, 41 % ± 8 saving (rerouting does most of it).
- 12-intersection city with a jam on the route: 114.0 to 79.1 s, 29 % ± 10.
- Adaptive vs fixed-time delay: 35 % reduction on balanced demand and on one heavy entrance.

Do not quote these as real-road results.

## Questions to prepare for

- *Is this real or simulated?* Answer with the built/simulated table, not adjectives.
- *Where is it weak?* Adaptive signals show no gain with three heavy entrances (1 % ± 14) or on the balanced city grid (6 % ± 8); rerouting around a moderate jam did not help (-10 % ± 15).
- *Why a camera and a marker instead of GPS?* The marker stands in for GPS on a tabletop; a GPS module is the production path.
- *Can the LLM change signals?* No. It can propose one change; the validator and an operator confirmation gate it, and emergency priority outranks it.
- *What about privacy?* Anonymous in-memory road-user sessions, fixed-template warnings that never include the vehicle's identity, route or position, scheduled purge of raw telemetry.
- *Isn't a phone while driving unsafe?* The driver view is a glanceable, no-typing display; in the demo it stands in for an in-vehicle unit.
- *V2V?* Out of scope for this prototype; the planned message types map to SAE J2735 (future work).
