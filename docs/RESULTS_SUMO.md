# Results: SUMO triangle (SIMULATED)

Generated 2026-10-30 10:31 UTC · 5 seeds · 60 s warm-up + 240 s measured per run. Mean ± standard deviation across seeds. SUMO's driver model plus our real controller; not a prediction for a real road.

## A. Adaptive signals vs fixed-time

| Demand | Signals | Vehicles served | Mean wait per vehicle (s) | Mean trip time (s) | Vehicles still in network |
|---|---|---|---|---|---|
| balanced demand (0.12 veh/s per entrance) | fixed-time | 80.6 ± 0.9 | 7.7 ± 0.3 | 69.1 ± 1.1 | 27.4 ± 0.9 |
| balanced demand (0.12 veh/s per entrance) | FlowSync | 83.8 ± 0.8 | 3.6 ± 0.4 | 62.2 ± 1.2 | 23.4 ± 1.1 |
| rush from the West (0.30 / 0.08 / 0.08) | fixed-time | 101.8 ± 1.5 | 7.8 ± 0.6 | 71.8 ± 1.0 | 35.8 ± 1.1 |
| rush from the West (0.30 / 0.08 / 0.08) | FlowSync | 105.0 ± 2.5 | 4.1 ± 0.5 | 63.9 ± 0.7 | 31.6 ± 1.7 |

## B. Ambulance West → South with a jam on the direct link (L13)

| System | Arrived | Trip time (s) | Seconds stopped | Stops | Route |
|---|---|---|---|---|---|
| baseline (no priority, no live reroute) | 5/5 | 69.4 ± 1.3 | 8.0 ± 0.0 | 1.2 ± 0.4 | W1 > L13 > O3 |
| FlowSync (priority + reroute) | 5/5 | 71.2 ± 7.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | W1 > L12 > L23 > O3 |

_Run time 755 s. Reproduce: `python -m sumosim.compare --seeds 5`._
