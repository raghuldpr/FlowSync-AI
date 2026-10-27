# FlowSync AI

A connected traffic-intelligence prototype for several intersections: signals that adapt to measured demand within deterministic safety rules,
incident detection with impact assessment and alternative routes, advance **location-aware** emergency-vehicle warnings that update, redirect or cancel,
separate public / operator access enforced and logged on the backend, and an LLM that reasons over tools but never controls anything.

Plan: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) (plan v2 and its status at the end).

**Status.** Everything below is built and tested in software (see the test count under *Run*): backend + device API, deterministic controller (starvation-safe, spillback / gridlock rules),
emergency-vehicle green corridor, routing, incidents and impact, road-user (phone) alerts with server-side relevance, road-condition hazards with a verification workflow, the LLM layer,
viewer / operator / admin roles, audit export, retention, health, metrics, scenario replay, a 12-intersection simulated city, a camera pipeline, and a self-checking demo with four failure drills.
The ESP32 firmware (`intersection`, `lamptest`, `roadcam`) **compiles for the real targets but has not been flashed or wired**; the vision pipeline is verified on a **synthetic** camera; the LLM only
against a stand-in model; the measured benefits in [docs/RESULTS.md](docs/RESULTS.md) are **simulated**. What remains is physical: [docs/HARDWARE_RUNBOOK.md](docs/HARDWARE_RUNBOOK.md).

Docs: [demo script](docs/DEMO_SCRIPT.md) · [built vs simulated](docs/BUILT_VS_SIMULATED.md) · [measured results](docs/RESULTS.md) · [hardware spec v2](docs/HARDWARE_SPEC_v2.md) · [software spec v2](docs/SOFTWARE_SPEC_v2.md) ·
[wiring](docs/HARDWARE_WIRING.md) · [vision setup](docs/VISION_SETUP.md) · [hardware runbook](docs/HARDWARE_RUNBOOK.md) · [pitch outline](docs/PITCH_OUTLINE.md).

Pages (served by the backend):
`/operator.html` (login; Scenario, Road conditions, Assistant, Replay, Built-vs-simulated) · `/presenter.html` (full-screen narration + live self-checks) ·
`/driver.html` (**phones**: join as a driver, see only alerts relevant to your route) · `/public.html?approach=L12` (per-approach warning view).

## Run (Windows PowerShell)
```powershell
python -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH="backend"
.\.venv\Scripts\python -m uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000   # backend (reachable by devices and phones)
.\.venv\Scripts\python -m simulator.run_sim                                                   # virtual tabletop (2nd terminal)
.\.venv\Scripts\python -m app.preflight --drill drill_llm_down                                # is it fit to demo? (3rd terminal)
.\.venv\Scripts\python -m pytest -q                                                            # tests; firmware / vision tests need requirements-dev.txt / requirements-vision.txt, else they skip
```
Then press **Start** in the Scenario panel (or `python -m simulator.run_sim --demo`). On a physical table the panel shows what a person must do at each step.
City view: `python -m app.gridgen`, then run the backend and simulator with `FLOWSYNC_TOPOLOGY_PATH=config/topology.grid.yaml` and `FLOWSYNC_DEVICES_PATH=config/devices.grid.yaml`.
Reproduce the measured results: `python -m simulator.evaluate --seeds 8 --city` (about 20 minutes).

Accounts (development defaults, change them; the health check warns): `viewer` / `operator` / `admin`, passwords from `FLOWSYNC_VIEWER_PASSWORD`, `FLOWSYNC_OPERATOR_PASSWORD`, `FLOWSYNC_ADMIN_PASSWORD`.
Device keys in `config/devices.yaml` are dev keys; replace them for a real demo.

## EV flow (Phase 2)
- Declare: `POST /api/operator/ev` (operator) or `POST /api/device/ev-declare` (car button).
- Position: `POST /api/device/ev-position {vehicle_id, edge, progress}` from the camera (ArUco) or simulator.
- Operator: `GET /api/operator/ev`, `/api/operator/ev/warnings`, `POST /api/operator/edges/{edge}/closed`.
- Public driver view: `GET /api/public/warnings?approach=L13` (fixed template + coarse ETA band only).

## LLM layer (Phase 5) — optional at runtime, never authoritative
Configure in `.env` (copy `.env.example`): `LLM_ENABLED=true`, `LLM_BASE_URL` (any OpenAI-compatible `/v1` endpoint), `LLM_MODEL`, `LLM_API_KEY`.
Without them every question is answered by the deterministic fallback (labelled `DETERMINISTIC FALLBACK` in the UI).
- Operator-only: `POST /api/llm/query`. Read tools: traffic, signals, incidents, impact, EV warnings, route alternatives, events.
- One write-capable tool, `propose_signal_adjustment`: offered only when the question asks for a change; the validator
  accepts/rejects, an operator must **Confirm** in the UI, the validator re-runs, and the controller still enforces
  min green / yellow / all-red. Emergency priority outranks it. Requests expire after ≤30 s.
- Tool output is cleaned (≤160 chars/string), wrapped as untrusted data, and every call is audit-logged; max 5 tool calls/query.
- The model never writes driver-facing text; warnings come only from fixed templates.
- **What leaves the laptop** when enabled: the operator's question plus tool results (queue counts, signal phases, incident
  summaries, EV route and warning states, recent event types). Not sent: API keys, tokens, EV position history, device keys, raw DB rows.
  Needs internet, so keep the fallback ready at the venue.

## Known gaps / decisions
- Gridlock rotation (done): when both axes are spillback-blocked, green rotates after `max_green_ms`. If only one axis is blocked, spillback still holds it (by design).
- Adaptive vs fixed-time gain in the sim is small near saturation (mean E2 queue 6.2 vs 6.8); needs a lighter-load comparison before any claim.
- EV priority uses the EV's current approach, and the next one past halfway; tune on the real table.
- Public (anonymous) role has no login; operator is the only account.
- Rate limiting exists only for login; no public POST endpoints yet.
- EV localisation (ArUco + homography) and ROI counting are verified only on a synthetic camera; see [docs/VISION_SETUP.md](docs/VISION_SETUP.md) before the first real-table run (per-lane capacities: `python -m app.vision.capacity`).
- On the physical table lane capacities are 2–6 vehicles, so the default congestion rule (75 % full for 10 s) fires at 2 of 3 cars on short lanes; tune `incidents:` in `config/topology.yaml` when you see the real dashboard.
- Run the camera demo with `python -m simulator.run_sim --demo --vision` against a backend started with `FLOWSYNC_TOPOLOGY_PATH` pointing at the file from `python -m app.vision.capacity --write`.
