# SUMO view (SIMULATED)

A realistic road-traffic world for the FlowSync triangle. The backend is unchanged: `SumoWorld` is a drop-in for
`simulator.tabletop.VirtualTabletop` and talks to it through the same signed device API. The Python tabletop remains the fallback.

```bash
pip install -r requirements-sumo.txt              # eclipse-sumo, traci, sumolib (bundles SUMO; no MSI needed)
python -m sumosim.build_network                   # topology.yaml -> sumosim/net/triangle.net.xml (run_sumo does this itself)
python -m uvicorn app.main:app_factory --factory --app-dir backend --port 8000     # terminal 1
python -m sumosim.run_sumo --gui --demo           # terminal 2: SUMO-GUI + the ambulance_demo scenario
python -m sumosim.compare --seeds 5               # fixed-timer vs FlowSync -> docs/RESULTS_SUMO.md
```

Options: `--demand calm|normal|rush`, `--speed 2`, `--scenario drill_camera_loss`, `--duration 120`, no `--gui` for headless.

What it does each simulated second: counts vehicles in each approach's camera zone (last 45 m) and posts them; feeds backend
commands to `SignalFSM` (min green / yellow / all-red) and maps the lamps onto the SUMO traffic lights; spawns traffic;
inserts the ambulance `EV-1` (vClass `emergency`) when the scenario drives it, reports its position and applies the route the
backend gives back (live rerouting). Scenario ops `reset, rates, pin, stall, camera, link, drive_ev` are implemented
(`pin` = a queue of parked vehicles, `stall` = one broken-down vehicle).

Colours in the GUI: red = ambulance, orange = scripted jam, purple = stalled vehicle.

Tests: `pytest backend/tests/test_sumo.py` (skipped if SUMO is missing). Files in `net/` are generated and git-ignored.
