# FlowSync AI

A connected traffic-intelligence prototype for several intersections, built for a hackathon. It demonstrates:

- signals that adapt to measured demand **inside deterministic safety rules** (min green, yellow, all-red, spillback and starvation rules);
- incident detection with impact assessment, alternative routes and operator alerts;
- advance, **location-aware emergency-vehicle (EV) warnings** that are shown only to relevant road users and that update, redirect or cancel when the route changes, the vehicle passes, or its location goes stale;
- separate public / operator access, enforced and audit-logged on the backend;
- an LLM that reasons over read tools and may *propose* one validated change, but never controls anything.

The target demo is a physical three-intersection tabletop (ESP32 controller with a 74HCT595 LED chain, an overhead phone camera, a toy car carrying an ArUco marker as the ambulance, and a laptop running this backend). Every software path also runs against a simulator, so the whole system can be demonstrated, tested and measured without hardware.

## Honest status

| Part | State |
|---|---|
| Backend, controller, EV corridor, routing, incidents, road-user alerts, hazards, roles, audit, retention, health, metrics, replay | built and covered by automated tests (373 pass) |
| Operator / presenter / phone dashboards | built, served by the backend |
| ESP32 firmware (`intersection`, `lamptest`, `roadcam`) | compiles for the real targets and passes host-side tests against Python golden traces; **not flashed or wired** |
| Camera vision (ROI counting, ArUco EV tracking) | verified on a **synthetic** camera only |
| LLM layer | tested against a stand-in model; deterministic fallback is always available |
| Measured benefits ([docs/RESULTS.md](docs/RESULTS.md)) | **simulated**: outcomes of our own traffic and driver models, not predictions for a real road |
| Credentials | development defaults; change them before any real demo (the health check warns) |

What remains is physical (wiring, flashing, table, camera calibration, rehearsal): see [docs/HARDWARE_RUNBOOK.md](docs/HARDWARE_RUNBOOK.md). What is real and what is simulated is also generated live in the dashboard's *Built vs simulated* tab and described in [docs/BUILT_VS_SIMULATED.md](docs/BUILT_VS_SIMULATED.md).

## Measured results (simulated, 8 seeds, mean ± sd)

Full tables, method and caveats: [docs/RESULTS.md](docs/RESULTS.md).

| Experiment | Baseline | System | Saving |
|---|---|---|---|
| Ambulance trip, stalled vehicle on the direct road | 95.6 ± 12.2 s | 56.0 ± 0.0 s (reroute + priority) | 41 % ± 8 |
| Ambulance trip, 12-intersection city, jam on its route | 114.0 ± 16.3 s | 79.1 ± 2.2 s | 29 % ± 10 |
| Ambulance, 6-vehicle jam on the direct road | 56.9 ± 3.6 s | priority only: 51.4 ± 0.5 s | 9 % ± 6 |
| Signal delay, balanced demand | 12.3 ± 1.5 s (fixed-time) | 7.8 ± 0.8 s (adaptive) | 35 % ± 9 |
| Signal delay, one heavy entrance | 14.0 ± 1.6 s | 9.0 ± 1.2 s | 35 % ± 13 |

Where the system does **not** show a benefit in our model, and we say so: rerouting around a moderate jam (-10 % ± 15), the free-flowing ambulance trip (2 % ± 5), adaptive signals with three heavy entrances (1 % ± 14), and adaptive signals on the balanced city grid (6 % ± 8). A saving whose spread is as large as its mean is not evidence.

## Quick start (Windows PowerShell)

Requirements: Python 3.11+ (developed on 3.14), Git. Hardware is optional.

```powershell
git clone https://github.com/raghuldpr/FlowSync-AI.git
cd FlowSync-AI
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH = "backend"
```

Run three things in three terminals (each needs `$env:PYTHONPATH = "backend"` and, for the simulator, the repository root as the working directory):

```powershell
# 1. backend (listens on all interfaces so devices and phones can reach it)
.\.venv\Scripts\python -m uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000

# 2. virtual tabletop (the simulated world)
.\.venv\Scripts\python -m simulator.run_sim

# 3. readiness check (should end with READY)
.\.venv\Scripts\python -m app.preflight --drill drill_llm_down
```

Open `http://localhost:8000/operator.html`, log in as `operator` (default password `operator-dev`), open the **Scenario** panel and press **Start** (or run `python -m simulator.run_sim --demo`). Put `http://localhost:8000/presenter.html` on a second screen for the narration and live self-checks.

On Linux or macOS use `source .venv/bin/activate`, `export PYTHONPATH=backend:.` and the same commands.

Before a phone-hotspot demo, print the join links and a QR sheet for the laptop's current address (needs `pip install -r requirements-demo.txt`):

```powershell
.\.venv\Scripts\python -m app.joinqr      # writes data/qr/join.html
```

### Pages (served by the backend)

| Page | For |
|---|---|
| `/operator.html` | operators: Scenario, Road conditions, Assistant, Replay, Built-vs-simulated, alerts, audit |
| `/presenter.html` | full-screen narration with live ✓/✗ self-checks |
| `/driver.html` | phones: join as a road user, see only alerts relevant to your route |
| `/public.html?approach=L12` | public per-approach warning view |
| `/index.html` | overview map |

### Run modes

| Mode | How |
|---|---|
| Fully simulated | backend + `python -m simulator.run_sim` |
| Simulated with a synthetic camera | `python -m simulator.run_sim --demo --vision` (needs `requirements-vision.txt`) |
| Physical table | backend + `python -m app.vision.run ...` + a flashed controller; the Scenario panel then shows what a person must do at each step |
| City (12 intersections, simulated) | `python -m app.gridgen`, then start backend and simulator with `FLOWSYNC_TOPOLOGY_PATH=config/topology.grid.yaml` and `FLOWSYNC_DEVICES_PATH=config/devices.grid.yaml`, and run the `city_demo` scenario |

## Tests and results

```powershell
.\.venv\Scripts\python -m pytest -q                  # 373 tests, about 8 minutes
.\.venv\Scripts\python -m pip install -r requirements-dev.txt      # firmware host tests (C++ core vs Python golden traces)
.\.venv\Scripts\python -m pip install -r requirements-vision.txt   # vision tests (OpenCV, numpy); otherwise they skip
.\.venv\Scripts\python -m simulator.evaluate --seeds 8 --city      # regenerate docs/RESULTS.md and dashboard/results.json (about 6 minutes)
```

`--quick` gives a smoke run; `--workers N` sets the number of worker processes.

## Configuration

Copy `.env.example` to `.env` (git-ignored) or set environment variables:

| Variable | Meaning |
|---|---|
| `FLOWSYNC_VIEWER_PASSWORD`, `FLOWSYNC_OPERATOR_PASSWORD`, `FLOWSYNC_ADMIN_PASSWORD` | account passwords (development defaults exist; **change them**) |
| `FLOWSYNC_DB_PATH` | SQLite file (default `flowsync.db`) |
| `FLOWSYNC_TOPOLOGY_PATH`, `FLOWSYNC_DEVICES_PATH` | topology / devices YAML (default `config/topology.yaml`, `config/devices.yaml`) |
| `FLOWSYNC_IMAGE_DIR` | where verified road-camera images are stored (24 h retention) |
| `LLM_ENABLED`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | optional LLM; any OpenAI-compatible `/v1` endpoint |

Device keys in `config/devices.yaml` are development keys; replace them for a real demo. Firmware `config.h` files (Wi-Fi, laptop address, device key) are git-ignored; copy from `config.example.h`.

## Design in brief

- **Backend-authoritative control.** Devices poll and POST signed JSON over HTTP (HMAC-SHA256 over method, path, device, boot id and an increasing sequence number, so replays are rejected). Every signal command passes the deterministic validator. After 5 s without a successful exchange the firmware falls back to a fixed-time cycle (always with yellow and all-red).
- **Controller.** Per-intersection pressure from queues minus downstream occupancy, spillback at 80 % of the downstream lane's capacity, demand-based starvation rules, gridlock rotation when both axes are blocked, min green 5 s, yellow 2 s, all-red 1 s, max green 20 s.
- **Emergency vehicles.** Sessions with a 4 s location TTL; a green corridor timed from the vehicle's ETA to each approach; warnings follow a lifecycle: active, updated, redirected, then cancelled (passed, route change, stale) or expired.
- **Routing and incidents.** Dijkstra over travel time plus queue and verified-hazard penalties; closures remove edges; hysteresis avoids route flapping. Incidents come with an impact assessment and an alternative path.
- **Road users.** Anonymous, in-memory sessions; relevance is decided on the server (the EV must come up behind the user within a short window on a road they are on or entering); warnings use fixed, lawful templates and never include the EV's identity, route or position.
- **Roles.** public, road-user, viewer, operator, admin; checked on the backend, with every denial audit-logged. Audit export neutralises spreadsheet formulas; raw telemetry is purged on a schedule (traffic 24 h, EV positions 6 h).
- **LLM.** Read tools plus one proposal tool. A proposal is validated, an operator must confirm, it is validated again, and the controller still enforces clearances. Emergency priority outranks it. Without a model every answer comes from a labelled deterministic fallback.

## Repository layout

```
backend/app/      FastAPI app: api/ (public, device, operator, llm, roadusers, hazards, admin), core/ (controller, ev, routing,
                  incidents, hazards, roaduser, simbus), llm/, vision/, scenarios.py, preflight.py, gridgen.py, health.py, metrics.py
backend/tests/    automated tests
config/           topology.yaml, devices.yaml (+ generated city grid files)
dashboard/        operator, presenter, phone driver, public and overview pages
simulator/        virtual tabletop (run_sim.py, tabletop.py), vision_world.py, evaluate.py (A/B experiments)
firmware/         intersection controller, lamp test, ESP32-CAM road camera, timing-header generator
docs/             plans, specs, runbooks, demo script, results
```

## Documentation

[Demo script](docs/DEMO_SCRIPT.md) · [Built vs simulated](docs/BUILT_VS_SIMULATED.md) · [Measured results](docs/RESULTS.md) · [Implementation plan](docs/IMPLEMENTATION_PLAN.md) · [Hardware spec v2](docs/HARDWARE_SPEC_v2.md) · [Software spec v2](docs/SOFTWARE_SPEC_v2.md) · [Wiring](docs/HARDWARE_WIRING.md) · [Vision setup](docs/VISION_SETUP.md) · [Hardware runbook](docs/HARDWARE_RUNBOOK.md) · [Car wiring](docs/HARDWARE_CAR_WIRING.md) · [Frontend spec](docs/FRONTEND_SPEC.md) · [Pitch outline](docs/PITCH_OUTLINE.md) · [Wow-factor tracker](docs/WOW_FACTORS.md) · [LLM setup](docs/LLM_SETUP.md)

## Building the firmware

PlatformIO environments: `esp32dev` (intersection controller), `lamptest`, and `esp32cam` (road camera). Copy `config.example.h` to `config.h` in the firmware project's `include/`, fill in Wi-Fi and the laptop address (printed by the preflight check), then:

```powershell
.\.venv\Scripts\python -m platformio run -d firmware/intersection -e esp32dev
.\.venv\Scripts\python -m platformio run -d firmware/intersection -e esp32dev -t upload
```

Wiring: [docs/HARDWARE_WIRING.md](docs/HARDWARE_WIRING.md). Vision and table calibration: [docs/VISION_SETUP.md](docs/VISION_SETUP.md).

## Known limitations

- The firmware has not run on hardware; vision accuracy on a real table is unmeasured.
- On the physical table lane capacities are 2-6 vehicles, so the default congestion rule (75 % full for 10 s) can fire at 2 of 3 cars on short lanes; tune `incidents:` in `config/topology.yaml`.
- Adaptive signals show no measured advantage under three simultaneous heavy entrances or on the balanced city grid; a parameter sweep (`control.switch_margin`, `timing.min_green_ms`) was started but not completed.
- Rerouting around a moderate jam does not help the ambulance in our model; it helps around a blocked road.
- Public POST endpoints (road-user join, hazard reports) are rate limited per IP and token, but the system has no CAPTCHA or account system for the public.
- The simulator test client opens one event loop per world. Do not revert that: on Windows a loop per request exhausts ephemeral ports and long evaluations hang.
- Development passwords and keys are for local use only.
