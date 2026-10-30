# SUMO view (SIMULATED)

The "how it works / evidence" screen for the demo: a road-traffic world for the FlowSync triangle in SUMO, driven by the
real backend. It is **not** the live source for judges' phones (those use the backend's own road-user simulation) and it is
independent of the physical table and the car. The backend is unchanged: `SumoWorld` is a drop-in for
`simulator.tabletop.VirtualTabletop` and talks to it through the same signed device API the ESP32s use.

```bash
pip install -r requirements-sumo.txt              # eclipse-sumo, traci, sumolib (bundles SUMO; no MSI needed)
python -m uvicorn app.main:app_factory --factory --app-dir backend --port 8000      # terminal 1: the backend
python -m sumosim.run_sumo --gui --demo           # terminal 2: SUMO-GUI + the ambulance_demo scenario
python -m sumosim.compare --seeds 5               # fixed timer vs FlowSync -> docs/RESULTS_SUMO.md (about 12 min)
```

The network (`sumosim/net/`, git-ignored) is generated from `config/topology.yaml` the first time (`python -m sumosim.build_network`).
Options for `run_sumo`: `--demand calm|normal|rush`, `--speed 2`, `--scenario drill_camera_loss`, `--duration 120`; no `--gui` = headless.
Colours in the GUI: red = ambulance, orange = scripted jam, purple = stalled vehicle.

## What it does each simulated second
- Counts vehicles in each approach's camera zone (last 60 m) and posts them as `simulated` traffic readings.
- Feeds the backend's signal commands to `SignalFSM` (min green / yellow / all-red) and writes only that FSM's lamps into SUMO.
  Nothing here decides signals.
- Spawns background traffic at the entrances.
- **Ambulance, any number of trips:** whenever the backend has an active session for `EV-1` (started by a scenario, the
  operator API `POST /api/operator/ev` or the dashboard's Emergencies screen), the ambulance is inserted on the backend's
  route, follows it if the backend reroutes, is reported back as edge + progress, and a final report lets the backend end the
  session. The next session starts the next trip.
- Scenario ops `reset, rates, pin, stall, camera, link, drive_ev` (`pin` = a crawling queue, `stall` = one broken-down vehicle).

## Results, honestly (SIMULATED, 5 seeds; full tables in `docs/RESULTS_SUMO.md`)
- **Waiting time roughly halves** with FlowSync vs the fixed timer: 3.6 s vs 7.7 s (balanced demand), 4.1 s vs 7.8 s (rush from the West).
- **The ambulance never has to stop** (0 s stopped vs about 8 s for the baseline), **but its trip is not shorter**: 71 s vs 69 s.
  The baseline overtakes the jam in the second lane; FlowSync takes a longer detour.
- These are SUMO's driver model plus our controller, not a prediction for a real road.

## Notes
- This replaces an earlier, separate SUMO bridge (`simulator/sumo_bridge.py`, a network generator in the backend and a
  `sumo/` folder). Its single-run numbers (ambulance 17 to 22 s vs 87 s with SUMO's default signals) were not like for like
  and are not used. A top-level `sumo/` folder would also shadow the `sumo` pip package, which is why this package is `sumosim`.
- Not carried over: SUMO networks for the 12-intersection grid (never run through a bridge). The grid is simulated by the Python simulator.
- Tests: `pytest backend/tests/test_sumo.py` (skipped when SUMO is not installed).
