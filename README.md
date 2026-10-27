# FlowSync AI

Plan: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). Status: Phases 0–6 done (backend core, device API, controller, virtual tabletop, EV tracking, routing, warnings, incidents, dashboards, LLM layer, intersection firmware core). Next: Phase 7 vision; compile/flash `firmware/intersection/src/main.cpp` once the ESP32 toolchain is installed (see [firmware/README.md](firmware/README.md)).

Dashboards (served by the backend): `/operator.html` (login), `/public.html?approach=L12` (driver view).
Timed demo: `python -m simulator.run_sim --demo` (congestion on L13 at 15 s, EV declared at 30 s, breakdown on L21 at 110 s).

## Run (Windows PowerShell)
```powershell
python -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH="backend"
.\.venv\Scripts\python -m uvicorn app.main:app_factory --factory --port 8000     # backend
.\.venv\Scripts\python -m simulator.run_sim --heavy E2                          # virtual tabletop (2nd terminal)
.\.venv\Scripts\python -m pytest -q                                             # tests (180; the firmware ones need `pip install -r requirements-dev.txt`)
```
Operator login: user `operator`, password from `FLOWSYNC_OPERATOR_PASSWORD` (default `operator-dev`, dev only).
Device keys in `config/devices.yaml` are dev keys; replace them for the real demo.

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
- EV positions are scripted in tests; real localisation (ArUco + homography) is Phase 7.
