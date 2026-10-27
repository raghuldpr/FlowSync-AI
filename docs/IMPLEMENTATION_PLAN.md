# FlowSync AI — Implementation Plan

Sources: `hardware.md` (HW), `softawre.md` (SW), and the review findings (C1–C9, H1–H14, M1–M13, L1–L6).
This plan is the single source of truth until HW/SW docs are re-issued (see Workstream 9).

---

## 0. Analysis summary

**What's sound and kept:** local-first FastAPI monolith, SQLite, deterministic controller with
safety validation, Dijkstra with non-negative costs, "LLM is never the safety authority",
honest labelling of fallbacks, device HTTP polling.

**What blocks the project as written (must be fixed in design before coding):**

| Area | Problem | Decision in this plan |
|---|---|---|
| LED drive (C1, M3) | 9 GPIOs for 18 LEDs; green dim at 3.3 V | 3× 74HCT595 @ 5 V, OE pulled up, all-red latched at boot |
| Control authority (C5) | HW says car→intersection ESP-NOW acts directly | **Backend is authoritative.** HTTP only. ESP-NOW dropped (Tier 3 degraded mode at most) |
| Localisation (C7) | Physical car has no position | ArUco marker on car, tracked by overhead phone camera + homography |
| Car role (C7) | EV or road user? | **Connected car = the EV.** Other road users' warnings shown on per-approach public views |
| Car power/size (C6) | Tethered to table bank, too small | Own power bank, 15–20 cm hand-pushed platform, 470–1000 µF cap on ESP32-CAM |
| Road layout (H1) | Undefined; no alternative route | **Triangle ring** (below) — every OD pair has 2 paths; internal links give downstream queues |
| Auth (C2) | None | `public` / `operator` roles, bcrypt + bearer token, `audit_log` incl. denials |
| EV warnings (C3) | Not designed | Warning lifecycle state machine + relevance rule + TTL staleness |
| LLM (C4) | "Optional" | Required, operator-only, non-authoritative; deterministic fallback stays |
| Scope (C9) | Unachievable | Tiered cut line (§2) + simulator-first build so software never waits on hardware |
| Clocks (H5) | Timestamps across unsynced ESP32s | `seq` + `valid_for_ms` measured on device `millis()`; backend stamps receive time |
| Phase monitoring (H7) | Commanded ≠ actual | Firmware reports actual phase + command ACK; dashboard flags mismatch/stale |

### Key architectural move: simulator-first
A **virtual tabletop** (Python) emulates the intersection ESP32, the EV car, and the camera's
ROI counts + ArUco positions, talking to the backend through the **same device APIs** as the real
hardware. All backend/dashboard/LLM/warning work proceeds and is demoable before any wire is
soldered. Real hardware later replaces simulator components one at a time. This also becomes the
honest "Simulated" fallback at the venue.

### Environment notes (this laptop)
- Python 3.14 is installed. Use a **Python 3.12 venv** for the CV stack (ultralytics/torch/opencv
  wheels are most reliable there) — verify before pinning.
- No Node.js → dashboard is **plain HTML + vanilla JS** (or Alpine/htmx via CDN) served by FastAPI. No build step.
- No git → install Git for Windows and `git init` before Phase 1.

---

## 1. Fixed design decisions

### 1.1 Tabletop topology (triangle ring)
```
          N1 (ext)                 N2 (ext)
            |                        |
   W1 ──► [ I1 ] ═══ L12 / L21 ═══ [ I2 ] ──► E2
             \\                      //
          L13 / L31             L23 / L32
                \\                //
                     [ I3 ]
                       |
                     S3 (ext)
```
- Links are two-way (L12 and L21 are separate directed edges).
- Each intersection has **2 conflicting movements**: A = ring-road axis, B = external/cross axis.
- EV demo trip: **W1 → S3**. Path 1: I1→L13→I3. Path 2: I1→L12→I2→L23→I3.
- Camera ROIs: one per inbound approach + one per internal link. Downstream queue for a movement =
  occupancy of the link it discharges into.
- Topology, ROIs, lengths, speeds, conflict matrix live in `config/topology.yaml` and are shared by
  controller, router, warnings, simulator, and (later) SUMO network generation.

### 1.2 Signal control
- Phases per intersection: `A_GREEN → A_YELLOW → ALL_RED → B_GREEN → B_YELLOW → ALL_RED`.
- Timings (tunable): min green 5 s, yellow 2 s, all-red 1 s, max green 20 s, max red 40 s.
- Yellow + all-red **mandatory** before any conflicting green, including EV preemption.
- Pressure: `P(phase) = Σ_m [Q_up(m) − Q_down(m)]`; stale input → `unknown`, never zero.
- Spillback: never green into a link with occupancy ≥ threshold.
- EV priority: request target phase through the same validator; no skipping clearance.
- Backend sends only a **target phase + hold**; firmware runs the transitions itself.

### 1.3 Device protocol (HTTP/JSON, polling)
- Header `X-Device-Id` + `X-Signature: HMAC-SHA256(key, device_id|seq|body)`; allowlisted IDs; replay rejected (seq must increase).
- Command: `{seq, intersection, target_phase, hold_ms, valid_for_ms, reason_code}`.
- Firmware: rejects old/duplicate seq; expires command `valid_for_ms` after *its own* receipt.
- `POST /api/device/command-ack` (accepted/rejected + reason), periodic `POST /api/device/state` (actual phases).
- Backend lost = no successful poll for 5 s → local fixed-time fallback cycle.

### 1.4 EV warnings
States: `active → updated | redirected → cancelled_passed | cancelled_route_change | cancelled_stale | expired`.
- Relevant approach = lies on EV's upcoming route within next N s (default 20 s), or is a queue on an approach the EV will use.
- EV position TTL 4 s → `cancelled_stale` ("EV location unknown") and priority request expires safely.
- Passing = EV crosses intersection stop-line ROI.
- Text from **fixed templates only**, e.g. *"Emergency vehicle approaching from the West. Make way only when it is safe and lawful to do so."* Test greps templates for forbidden phrasing.
- Public view exposes approach/direction + ETA band only. No track history, no telemetry.
- Target latency ≤ 3 s EV state change → warning update; measured.

### 1.5 Roles & audit
- Public: read redacted traffic, signals, active warnings for an approach, submit complaint (Tier 3).
- Operator: everything else — EV declare/clear, signal requests, incidents ack/resolve, sim triggers, LLM, raw telemetry.
- `audit_log(actor, role, action, target, params, result, ts)` for every restricted action **and every denial**.

### 1.6 LLM layer (required, non-authoritative)
- One OpenAI-compatible client (`base_url`, `model`, `api_key` from `.env`) — works for xAI, Gemini compat, OpenRouter.
- Tools (read): `get_traffic_state`, `get_signal_states`, `get_active_incidents`, `get_incident_impact`,
  `get_active_warnings`, `get_route_alternatives`, `get_system_events`.
- Tool (proposal): `propose_signal_adjustment` → deterministic validator accepts/rejects → explained.
- Role checked against the **human caller**; write/proposal tools need operator UI confirmation; max 5 tool calls/request;
  retrieved text wrapped as untrusted data; only aggregated context leaves the laptop; all calls audit-logged.
- LLM never writes driver-facing text. If LLM unreachable → deterministic summary + "LLM unavailable" label.

---

## 2. Scope tiers (cut line)

| Tier | Items |
|---|---|
| **1 — demo-critical** | Topology config · signal controller + validator · intersection firmware (595, state machine, fallback) · device API w/ HMAC + ACK/state · virtual tabletop simulator · camera ROI occupancy · ArUco EV tracking · Dijkstra + rerouting w/ hysteresis · EV warning lifecycle + public per-approach views · roles + audit log · incidents (`congestion_spike`, `stalled_vehicle`) + impact + operator alerts · LLM (1 provider, read tools + 1 proposal tool) · operator dashboard · demo script + Built-vs-Simulated table |
| **2** | Pothole pipeline (ESP32-CAM → model → hazard geotagged with ArUco pos) · YOLO secondary vehicle detector · SUMO scale-up view mirroring the triangle · intersection ultrasonic as stop-line fallback |
| **3 — drop if late** | Complaints · MPU6050 motion anomaly · car HC-SR04 · multi-provider failover · ESP-NOW degraded mode · blockchain |

---

## 3. Repository layout
```
FlowSync/
  backend/
    app/
      main.py              # FastAPI app, static dashboard mount
      config.py            # .env + topology.yaml loading
      db.py                # SQLite (WAL, single writer queue)
      auth.py              # users, tokens, role dependency
      audit.py
      models/              # pydantic schemas
      api/                 # public.py, operator.py, device.py, llm.py
      core/
        topology.py        # graph, ROIs, conflict matrix
        controller.py      # pressure controller
        validator.py       # safety rules (shared by controller, EV, LLM proposals)
        routing.py         # Dijkstra + hysteresis
        ev.py              # EV state, TTL, passing detection
        warnings.py        # lifecycle + relevance + templates
        incidents.py       # detection + impact assessment
      llm/                 # client, tools, orchestrator, fallback
      vision/              # camera source, ROI occupancy, aruco, homography, (yolo, pothole)
    tests/
  simulator/               # virtual tabletop: fake devices + fake camera feed
  firmware/
    intersection/          # ESP32 (Arduino/PlatformIO)
    car/                   # ESP32 EV car
    cam/                   # ESP32-CAM
  dashboard/               # operator.html, public.html, js, css
  config/topology.yaml
  sumo/                    # Tier 2
  docs/                    # this plan, demo script, revised HW/SW specs
  .env.example
```

---

## 4. Workstreams and order

Each phase ends with something runnable. ☐ = done-when criteria.

### Phase 0 — Scaffold (≈1 h)
- Repo layout, venv, `requirements.txt`, `.env.example`, `topology.yaml` for the triangle, run script.
- ☐ `uvicorn` serves `/api/health` and an empty dashboard page.

### Phase 1 — Core backend + simulator (≈4 h)
- DB schema: `users, audit_log, traffic_snapshots, signal_decisions, device_status, device_commands,
  ev_positions, ev_events, ev_warnings, incidents, routes, system_events` (+ `hazards`, `complaints` later).
- Auth + role dependency + audit (denials logged).
- Device API with HMAC/seq/allowlist; command polling; ACK; actual-state reporting.
- Controller + validator + per-intersection timing; decisions logged with reasons.
- Virtual tabletop: simulated intersection device (obeys the same firmware state machine, written once in
  Python as reference for the C++ port), simulated ROI occupancy, scripted scenarios.
- Tests: no conflicting greens ever; clearance always present; stale → unknown; spillback respected; replay rejected.
- ☐ Simulator runs a congestion scenario; signals adapt; dashboard JSON shows device-reported phases.

### Phase 2 — EV, routing, warnings (≈3 h)
- EV declare/clear (operator), position ingest with TTL, passing detection.
- Dijkstra on topology with live edge costs `T_e = L_e/V_e + D_e`, closures remove edges, 15% hysteresis.
- Warning lifecycle + relevance + templates; public `GET /api/public/warnings?approach=`.
- Tests: reroute → `redirected`; pass → `cancelled_passed`; no position for 4 s → `cancelled_stale`; template phrase test.
- ☐ Scripted EV trip W1→S3 with congestion on L13 triggers reroute via I2 and warnings move accordingly.

### Phase 3 — Incidents (≈1.5 h)
- `congestion_spike` (occupancy > θ for T s), `stalled_vehicle` (stationary in lane ROI > N s), generic model.
- Impact: affected edges/intersections, added delay from cost delta, routes affected, Dijkstra alternative.
- Operator alerts with acknowledge/resolve (audited).
- ☐ Simulated stall on L12 produces alert with impact + alternative.

### Phase 4 — Dashboards (≈3 h)
- Operator (login): triangle map with live phases (device-reported, mismatch/stale badges), queues,
  EV route + warnings, incidents panel, decision/event log, LLM chat, Built-vs-Simulated table.
- Public (no login): per-approach "driver view" with active warning only.
- Polling: 1 s operator, 500 ms public warnings.
- ☐ Full Phase 2+3 scenario visible end-to-end in browser.

### Phase 5 — LLM layer (≈2 h)
- Client, tool registry with schemas, orchestrator (5-call cap, timeout), proposal flow with UI confirmation, fallback.
- Tests: disabled mode works; public role denied; read-only intent can't trigger proposal; no key in logs.
- ☐ "Why is I2 congested and what should the EV do?" answered from real tool outputs.

### Phase 6 — Firmware (parallel, hardware dev) (≈5 h)
- **Intersection:** 3×74HCT595 (DATA 23, CLK 18, LATCH 19, OE 21 w/ 10 kΩ pull-up), signal task (FreeRTOS, fixed tick)
  separate from network task, all-red at boot, seq/valid_for_ms handling, ACK + state reports, 5 s fallback, task watchdog.
  Ultrasonic (Tier 2): TRIG 4/13/14, ECHO 34/35/36 via dividers, interrupt echo, staggered 60 ms.
- **Car (EV):** button (GPIO 32, pull-up, debounce) → `POST /api/device/emergency`; status LEDs; buzzer via NPN (1 kΩ base, diode).
- **ESP32-CAM (Tier 2):** QVGA/VGA JPEG every 3 s → `/api/device/images`.
- Wi-Fi: dedicated 2.4 GHz WPA2 router/hotspot, fixed channel, laptop DHCP reservation or mDNS.
- ☐ Real intersection board replaces simulated one; dashboard shows its ACKs and actual phases.

### Phase 7 — Vision (≈4 h)
- Camera source interface: Android IP-camera MJPEG URL **or** recorded file **or** simulator.
- Homography calibration (4 board-corner ArUco markers), ROI occupancy vs reference empty frame (primary),
  ArUco EV tracking → `ev_positions`. Lock phone exposure/focus; rigid mount; 2–5 FPS worker process.
- Go/no-go smoke test on the real feed in the first hour of this phase. YOLO only as Tier 2 secondary.
- ☐ Moving the ArUco car by hand moves the EV on the dashboard and drives warnings.

### Phase 8 — Tier 2 extras (time permitting)
- Pothole: isolated venv/process for the checkpoint; printed real-pothole photos as props; geotag with ArUco position; "simulated" injection fallback.
- SUMO: generate network from `topology.yaml`; EV `vClass="emergency"`; `setRedYellowGreenState` only after validator approval.

### Phase 9 — Demo hardening & docs (≈2 h)
- Timed demo script: congestion at I2 → incident → operator alert → LLM explains → EV declared → warnings on relevant
  approaches → congestion causes reroute → warnings redirect/cancel → EV passes → priority clears.
- Failure drills: backend down, Wi-Fi drop, camera loss, LLM down.
- Re-issue HW/SW docs: full pin map, correct BOM totals (M1/M2), §13 reliability/security, rendered diagrams, formulas.
- Data retention: purge EV positions/telemetry at session end.

---

## 5. Two-developer split

| Dev A — hardware / firmware / vision | Dev B — backend / dashboard / LLM |
|---|---|
| Procurement delta, wiring, Phase 6, Phase 7 | Phases 0–5 (simulator-first) |
| Owns `firmware/`, `backend/app/vision/` | Owns everything else in `backend/`, `dashboard/`, `simulator/` |

Integration checkpoints: (1) device API contract frozen after Phase 1; (2) real intersection board on backend;
(3) ArUco feed replaces simulated EV position; (4) full rehearsal.

---

## 6. Hardware procurement delta
Add: 3× 74HCT595 (+0.1 µF each), 2N2222/BC547 + 1 kΩ + 1N4148 for buzzer, 470–1000 µF electrolytic,
second small power bank (car), 15–20 cm car platform, printed ArUco markers (car + 4 corners),
dedicated 2.4 GHz router (if no reliable hotspot), green-LED resistors 150–220 Ω @ 5 V.
Optional: HC-SR04P/RCWL-1601 (3.3 V) to skip dividers. Drop for Tier 1: car HC-SR04, MPU6050.

---

## 7. Open assumptions (change if wrong)
1. Hackathon timing allows building now; hardware may not be on hand yet → simulator-first.
2. One LLM provider is available with a key (any OpenAI-compatible endpoint).
3. Dashboard without a JS build step is acceptable.
