# Measured results (simulated)

Generated 2026-10-27T15:34:53+00:00 · 8 random seeds · produced by `python -m simulator.evaluate`.

> **Everything here is simulated.** These are outcomes of this project's traffic model and its ambulance-driver model (an ambulance slows by `1/(1+0.15×queue)` on queued approaches, creeps at a quarter speed past a stalled vehicle, and waits at the stop line for a green). They show what the system does under that model and how much it varies across seeds; they are **not** a prediction for a real road, and the physical table has not been measured yet.

## A. Ambulance trip, West → South (travel time in simulated seconds, mean ± sd over seeds)

**free-flowing roads**

| arm | route | travel time (s) | saving vs baseline | time stopped at red (s) | stops | arrived |
|---|---|---|---|---|---|---|
| baseline | W1 › L13 › O3 | 38.9 ± 2.1 | — | 0.8 ± 2.1 | 0.1 | 8/8 |
| reroute only | W1 › L13 › O3 | 38.9 ± 2.1 | 0 % ± 0 | 0.8 ± 2.1 | 0.1 | 8/8 |
| priority only | W1 › L13 › O3 | 38.0 ± 0.0 | 2 % ± 5 | 0.0 ± 0.0 | 0.0 | 8/8 |
| full system | W1 › L13 › O3 | 38.0 ± 0.0 | 2 % ± 5 | 0.0 ± 0.0 | 0.0 | 8/8 |

**a jam (6 vehicles) on the direct link L13**

| arm | route | travel time (s) | saving vs baseline | time stopped at red (s) | stops | arrived |
|---|---|---|---|---|---|---|
| baseline | W1 › L13 › O3 | 56.9 ± 3.6 | — | 6.2 ± 4.4 | 1.0 | 8/8 |
| reroute only | W1 › L12 › L23 › O3 | 62.0 ± 6.6 | -10 % ± 15 | 6.6 ± 7.2 | 1.4 | 8/8 |
| priority only | W1 › L13 › O3 | 51.4 ± 0.5 | 9 % ± 6 | 0.0 ± 0.0 | 0.0 | 8/8 |
| full system | W1 › L12 › L23 › O3 | 56.0 ± 0.0 | 1 % ± 7 | 0.0 ± 0.0 | 0.0 | 8/8 |

**a stalled vehicle blocking the direct link L13**

| arm | route | travel time (s) | saving vs baseline | time stopped at red (s) | stops | arrived |
|---|---|---|---|---|---|---|
| baseline | W1 › L13 › O3 | 95.6 ± 12.2 | — | 5.8 ± 6.1 | 0.6 | 8/8 |
| reroute only | W1 › L12 › L23 › O3 | 58.5 ± 4.9 | 38 % ± 11 | 2.5 ± 4.9 | 0.2 | 8/8 |
| priority only | W1 › L13 › O3 | 86.5 ± 4.9 | 9 % ± 7 | 0.0 ± 0.0 | 0.0 | 8/8 |
| full system | W1 › L12 › L23 › O3 | 56.0 ± 0.0 | 41 % ± 8 | 0.0 ± 0.0 | 0.0 | 8/8 |

## B. Adaptive vs fixed-time signals (all intersections)

Delay is the mean time a vehicle spends in an approach queue (Little's law: mean queue ÷ throughput). Fixed-time = the controllers' own fallback cycle.

| demand pattern | control | mean delay (s) | mean queue (veh.) | vehicles served | delay change (paired) |
|---|---|---|---|---|---|
| balanced demand | fixed-time | 12.3 ± 1.5 | 20.48 | 249 | — |
| balanced demand | adaptive | 7.8 ± 0.8 | 14.06 | 271 | +35 % ± 9 (reduction) |
| skewed demand (one heavy entrance) | fixed-time | 14.0 ± 1.6 | 9.35 | 101 | — |
| skewed demand (one heavy entrance) | adaptive | 9.0 ± 1.2 | 8.36 | 139 | +35 % ± 13 (reduction) |
| three heavy entrances | fixed-time | 13.6 ± 1.2 | 24.70 | 272 | — |
| three heavy entrances | adaptive | 13.5 ± 1.6 | 24.85 | 276 | +1 % ± 14 (reduction) |

## C. Simulated city (12 intersections, 62 roads)

Same controller and state machine as the tabletop, on the generated grid (`python -m app.gridgen`). Ambulance from West 1 to East 3 with a jam on the second road of its free-flow route.

| arm | travel time (s) | saving vs baseline | time stopped at red (s) | stops | arrived |
|---|---|---|---|---|---|
| baseline | 114.0 ± 16.3 | — | 20.6 ± 11.2 | 2.2 | 8/8 |
| full system | 79.1 ± 2.2 | 29 % ± 10 | 0.9 ± 2.5 | 0.1 | 8/8 |

| demand pattern | control | mean delay (s) | mean queue (veh.) | vehicles served | delay change (paired) |
|---|---|---|---|---|---|
| east-west tide (three heavy west entrances) | fixed-time | 10.1 ± 0.5 | 59.70 | 708 | — |
| east-west tide (three heavy west entrances) | adaptive | 8.9 ± 0.6 | 52.01 | 697 | +11 % ± 7 (reduction) |
| balanced demand | fixed-time | 12.5 ± 0.6 | 102.85 | 990 | — |
| balanced demand | adaptive | 11.7 ± 0.9 | 99.53 | 1022 | +6 % ± 8 (reduction) |

## How to read this

* A saving whose ± is larger than its mean is not evidence of a benefit.
* Each arm sees the same random arrivals for a given seed, so comparisons are paired.
* Reproduce: `python -m simulator.evaluate --seeds 8` (about 10 minutes); `--quick` for a smoke run.
