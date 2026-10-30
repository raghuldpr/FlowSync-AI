# SUMO view

SUMO is an optional, **simulated** road world for the FlowSync triangle. It is the "how it works / evidence" screen: fixed timer
vs FlowSync, ambulance priority and rerouting, shown in SUMO-GUI. The controller, state machine, validator, routing and warnings
are the FlowSync code; SUMO supplies vehicles.

Everything about running it, what it does and the results is in [sumosim/README.md](../sumosim/README.md); the measured tables are in
[RESULTS_SUMO.md](RESULTS_SUMO.md). Install: `pip install -r requirements-sumo.txt` (bundles SUMO) or `winget install --id EclipseFoundation.SUMO`.

Status: bridge, network generated from `topology.yaml`, ambulance trips, scenario ops and the fixed-timer comparison are built and
tested (`backend/tests/test_sumo.py`, skipped without SUMO). The SUMO-GUI window itself has not been looked at in an automated test.
Not done: SUMO networks for the 12-intersection grid; moving SUMO vehicles are not drawn in the 3D dashboard.
