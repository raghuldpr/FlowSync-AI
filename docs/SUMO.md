# SUMO scale-up view (Phase 8, Tier 2)

SUMO is an optional, simulated road world that mirrors the same topology the backend controls. It is **simulated** in
`BUILT_VS_SIMULATED.md` terms; the controller, state machine, validator, routing and warnings stay the FlowSync code.

## Status

| Piece | State |
|---|---|
| Network generator from any topology file (`backend/app/sumogen.py`) | **Done**, 16 tests (`test_sumogen.py`, each structural check runs on the triangle and the grid) |
| Generated triangle network (`sumo/triangle/`) | **Done**; builds with `netconvert` (SUMO 1.28.0) |
| TraCI bridge (`simulator/sumo_bridge.py`) | **Done**, 13 tests (`test_sumo_bridge.py`: pure helpers, and the real backend driven through a fake TraCI), plus live runs with real SUMO below |
| `sumo-gui` view | Supported (`--gui`), not opened in this session |
| 12-intersection grid in SUMO | Generator output tested; not run through the bridge |

## Install (Windows)

```powershell
winget install --id EclipseFoundation.SUMO --exact
```

The installer sets `SUMO_HOME` (`C:\Program Files (x86)\SUMO\`) and adds `bin` and `tools` to `PATH`; open a new terminal to
pick them up. `traci` and `sumolib` ship in `%SUMO_HOME%\tools`; the bridge adds that folder itself if `import traci` fails.

## Run it

```powershell
# from the repo root, three terminals (PYTHONPATH = "backend;.")
python -m app.sumogen --topology config/topology.yaml --out sumo/triangle --build     # once: XML + netconvert
python -m uvicorn app.main:app_factory --factory --port 8000                            # the backend
python -m simulator.sumo_bridge [--gui] [--speed 1] [--duration 240]                    # the SUMO world
```

Then declare an ambulance (dashboard Emergencies screen, operator API `POST /api/operator/ev`, or the car's button). Open the
3D dashboard (`cd web && npm run dev`), sign in, and the map shows the SUMO queues, the real lamps and the ambulance.

Generator options: `--scale` (metres per layout unit, default 0.5), `--speed`, `--veh-per-hour` (background cars per entrance),
`--ev-depart`. Output in `sumo/<name>/`: `flowsync.nod.xml`, `flowsync.edg.xml`, `routes.rou.xml` (shortest U-turn-free routes,
one background flow per entrance), `ev_baseline.rou.xml` (a scripted ambulance for no-backend runs only), `flowsync.sumocfg`
(0.1 s steps, teleporting off), `flowsync_map.json` (axis lists, routes, timings for the bridge).

## How the bridge works

It plays the role `simulator/tabletop.py` plays, through the same signed device API, so the backend cannot tell the difference.

- **SUMO to backend.** Every simulated second: queued vehicles (speed under 0.1 m/s) per approach, capped at the approach
  capacity, with the longest waiting time, posted as traffic readings with source `simulated`. The reference `SignalFSM` state is
  posted as the intersection state.
- **Backend to SUMO, signals.** Backend commands go to the same `SignalFSM` the firmware mirrors (min green, yellow, all-red).
  Only that FSM's lamps are written into SUMO (`setRedYellowGreenState`, phase held for a day so SUMO's own program cannot
  override it). Link order is read from the built network with `sumolib`; each link joins the axis (A or B) of the approach it
  leaves; left turns get lowercase `g` so they yield. The bridge never switches a signal on its own.
- **Backend to SUMO, ambulance.** When the backend has an active session for `EV-1`, the vehicle is inserted on the backend's
  route, follows it if the backend reroutes (`setRoute` from its current edge), is reported back as edge + progress, and is
  removed if the session ends. On arrival the bridge sends a final report so the backend ends the session as `arrived`.
- **Scenario commands.** `camera` and `link` are honoured. `pin`, `stall` and `rates` belong to the queue model and are logged
  once and ignored, so the scripted `ambulance_demo` scenario's congestion steps do not happen in SUMO.

## Results (single runs, SUMO 1.28.0, triangle network, 300 cars/h per entrance)

| Run | Ambulance `W1 L13 O3` | Stops | Waiting |
|---|---|---|---|
| SUMO's default signals, no backend (`ev_baseline.rou.xml`) | 87.2 s | 2 | 57.4 s |
| Backend steering the signals (bridge, run 1) | 21.6 s | 0 | 0 s |
| Backend steering the signals (bridge, run 2) | 16.8 s | 0 | not recorded in the log line |

The backend decisions during run 1 included `emergency_holding_green`, and the bridge saw no unsafe lamp combination.
These are one or two runs each, with different background traffic, so treat the gap as indicative, not a measured benchmark.
For a real A/B, run several seeds each way and report the mean and spread (SUMO takes `--seed`; the bridge does not pass one yet).

## Known limits

- The backend knows queues and the ambulance, not every SUMO vehicle, so the 3D dashboard shows queued cars and the ambulance,
  not the moving background traffic. Showing it needs a new device endpoint for vehicle positions.
- Provenance lists SUMO's data as "Simulated" (the traffic source enum has no `sumo` value); no backend change was made.
- `--speed` above 1 makes SUMO run faster than the backend's real-time controller loop; keep it at 1 for a fair comparison.
