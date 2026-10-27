# FlowSync AI — Software specification v2

Status: **implemented and tested** (automated tests + browser checks; see §9). Supersedes `softawre.md`. Where this document says
"built" it means code exists and tests pass; real-hardware and real-camera behaviour is **not** verified.

## 1. Architecture
Local-first modular monolith: one FastAPI backend, SQLite (WAL), a dependency-free web dashboard, and separate processes for the
camera pipeline (`python -m app.vision.run`) and the virtual tabletop (`python -m simulator.run_sim`). Everything is HTTP/JSON on the
local network. Control path: sensors/camera → backend (validated) → command → device firmware (final gate) → lamps. The LLM is
outside this path.

```
phone camera ──► vision pipeline ──► POST /api/device/{traffic,ev-position}  ┐
ESP32 controller ◄── GET /commands ─────────────────────────────────────────┤  FastAPI backend
                 ──► POST /state, /command-ack ─────────────────────────────┤  controller · validator · routing · incidents ·
operator dashboard / presenter / assistant  ◄──► /api/operator/*, /api/llm  ┤  EV manager · scenarios · LLM tools · audit
public driver view ◄── /api/public/*  ──────────────────────────────────────┘
```

## 2. Roles and access (enforced on the backend, logged)
| Audience | How identified | What they get |
|---|---|---|
| **public** | anonymous | lamp colours, coarse congestion bands, one fixed-text warning per approach, static map geometry |
| **road user** | anonymous session: random token + pseudonym ("Car 7"), memory-only, ends at arrival / 10 min idle | only their own feed: fixed-text alerts relevant to their route, a route suggestion, a hazard report button. Never anything about other users or the emergency vehicle's id/route/position |
| **viewer** | login (scrypt hash) → bearer token (8 h) | read-only operational data (traffic, signals, incidents, hazards, metrics, health, replays, audit) |
| **operator** | same | everything a viewer sees **plus** every action that changes the system: EVs, closures, incidents, hazards, proposals, scenarios, the assistant |
| **admin** | same | operator + users list, audit CSV export, retention |
| **device** | allow-listed id + HMAC-SHA256 over `METHOD|path|id|boot|seq|body`, increasing `seq` per boot id, used boot ids rejected | role-limited device endpoints (`intersection_controller`, `camera`, `ev_car`, `simulator`) |

Every restricted action — **and every denial** — is written to `audit_log` (actor, role, action, target, params, result, time). Login
failures are rate-limited (5/min per address) and audited; public POSTs are rate-limited (road-user join 10/min per address; hazard reports 5/min per session, 20/min per address); public GETs are capped per address; request bodies above 1 MB are refused. Not implemented: TLS (LAN HTTP).

## 3. API summary
**Public:** `GET /api/health`, `/api/public/{topology,signals,congestion}`, `/api/public/warnings?approach=`; road users: `POST /api/public/road-users`, `GET|DELETE /api/public/road-users/{token}`, `GET …/poll` (long-poll push), `POST …/route/accept`; `POST /api/public/hazards` (a report at the user's own position).
**Operator/viewer reads:** `POST /api/auth/login`; `GET /api/operator/{overview,traffic,signals,decisions,events,audit,devices,provenance,scenarios,scenario,proposals,incidents,ev,ev/warnings,hazards,images/{id},road-users,metrics,health,replays,replays/{id}}`.
**Operator writes:** `POST /api/operator/{hazards,hazards/{id}/verify|reject|resolve}`;
`POST /api/operator/{ev,ev/{id}/clear,edges/{edge}/closed,incidents,incidents/{id}/ack|resolve,proposals/{id}/confirm|decline,scenario/start,scenario/stop}`;
`GET /api/llm/status`, `POST /api/llm/query`.
**Admin:** `GET /api/admin/{users,audit.csv}`, `POST /api/admin/retention/run`.
**Device:** `POST /api/device/{traffic,state,command-ack,ev-position,ev-declare,images}`, `GET /api/device/{commands,sim-commands,sim-ev}`. Roles: `intersection_controller`, `camera`, `ev_car`, `road_camera`, `simulator`.
Inputs are validated (pydantic; counts ≤ 64, known approach/edge ids, bounded strings).

## 4. Data model (SQLite)
`users, sessions, audit_log, traffic_snapshots, signal_decisions, device_commands, device_status, emergency_requests, system_events,
ev_sessions, ev_positions, ev_warnings, ev_warning_events, incidents, hazards, images, llm_proposals, replay_runs, replay_frames`. Road-user sessions are **memory-only**. Live state (latest readings, device-reported
state, commands) is in memory. Timestamps are ISO-8601 UTC from the backend; devices never supply wall-clock time.

## 5. Algorithms
**Topology.** Triangle ring: 3 intersections, 2 movements each (A ring axis, B external axis), 6 ring links + 3 external stubs, each
with a camera ROI; `config/topology.yaml` is the single source (timings, capacities, vision geometry, layout).

**Signal control (deterministic).** Per intersection, per cycle, in this order:
1. stale data → *unknown* (never zero); no device report → hold;
2. pressure of an axis: `P(axis) = Σ_a max(0, Q_a − max_{d ∈ down(a)} Q_d)`; an approach whose downstream link is ≥ 80 % of *its* capacity contributes 0 (spillback);
   a stale downstream reading skips the approach (downstream pressure is required);
3. if both axes are blocked by spillback and green has reached `max_green` → rotate (`gridlock_rotation`);
4. emergency priority → target the EV's axis through the same validator; operator-confirmed requests next;
5. otherwise switch only after min green and when the other axis' pressure exceeds the current by a margin, or the current axis has **no demand** while the other has some, or max green is reached while the other axis has **demand**.
   *Demand* = vehicles waiting on approaches whose downstream is known and not full; it is separate from pressure (clamped at 0) so a lone vehicle behind a congested link is still served within max green
   — a starvation bug found by the A/B evaluation (an ambulance had waited 230 s at a stop line).
The device then performs GREEN → YELLOW (2 s) → ALL-RED (1 s) → GREEN; no path skips clearance.

**EV green corridor.** For each approach on the EV's remaining route, priority is requested once the EV is within `lead = min_green + yellow + all_red + 1 s + 1.4 s × queued vehicles` route-seconds, so the intersection has time to finish its green, clear and discharge the queue before the EV arrives.

**Road users.** A session drives a virtual car along its planned route (dead reckoning). *Relevance to an emergency vehicle*, decided on the server: the EV will come up **behind** the user — the user is on a road the EV will enter later (or on the same road ahead of it) within the 20 s window; users on other roads or on the opposite lane get nothing. Alert states follow the warning lifecycle (`active → updated | redirected → cancelled_passed | cancelled_route_change | cancelled_stale | expired`), and a position report from the EV refreshes every user's alert in the same request (measured: `ev_position_to_alert_ms`). Incidents and verified hazards on the user's route raise fixed-text alerts; a closed road ahead forces an automatic reroute with a notice; a congestion-driven alternative is only **suggested** (≥ 15 % and ≥ 3 s better) and the user accepts it.

**Road conditions.** Hazard (`pothole | debris | wet_surface`) lifecycle: `reported → open | rejected`, `open → resolved`; only an operator-verified hazard has effect: a travel-time penalty `H_e` of 3 / 8 / 15 s (low / medium / high; summed per road, capped at 30 s) in the same cost function as congestion, plus alerts. Sources: operator, public report (location = the user's own position, never client-supplied), road-camera heuristic (dark-patch finder; every detection is an unreviewed report).

**Scale.** `python -m app.gridgen` generates an R×C grid (default 3×4 = 12 intersections, 62 roads) in the same format; impact assessment uses one shortest-path tree per origin.

**Routing.** Dijkstra on the directed road graph, `T_e = L_e / V_e + D_e + H_e` seconds with `D_e = 3 s × queued vehicles` and `H_e` the verified-hazard penalty; closures (operator or stalled-vehicle incident)
remove the edge from the search; all weights finite and non-negative. A reroute needs the new remaining route to be > 15 % cheaper and ≥ 5 s since the last one;
a closure or deviation reroutes immediately.

**Incidents.** `congestion_spike`: occupancy ≥ 75 % for 10 s (clears below 50 % for 5 s). `stalled_vehicle`: object stationary ≥ 10 s while its own axis has been green ≥ 10 s and
its downstream link is not full; closes the edge for routing. Impact assessment: best route for every external origin→destination with and without the incident → affected roads and intersections, routes affected, added delay, alternatives, unreachable pairs.

**Emergency-vehicle warnings.** Relevant = approaches on the EV's remaining route reached within 20 s. States: `active → updated | redirected → cancelled_passed | cancelled_route_change | cancelled_stale | expired`,
every transition logged. EV location older than 4 s → stale → warnings cancelled and priority withdrawn. Driver-facing text is **only** the fixed template
*"Emergency vehicle approaching from …. Make way only when it is safe and lawful to do so."* (a test forbids unsafe phrasing).

## 6. LLM layer (required for the brief, never authoritative)
OpenAI-compatible client, operator-only. Seven read tools (traffic, signals, incidents, impact, warnings, route alternatives, events) and one proposal tool
(`propose_signal_adjustment`) offered only when the question asks for a change. Tool arguments are schema-validated, results cleaned and wrapped as untrusted data,
≤ 5 tool calls, deadline-bound, every call audit-logged. A proposal is validated, then confirmed by a human, then re-validated, then applied as a ≤ 30 s request that still obeys min green and clearance;
emergency priority outranks it. Any failure → a labelled deterministic answer built from the same tools. Verified against a stand-in model only.

## 7. Scenarios, drills, provenance
`app/scenarios.py`: timed scripts with narration, backend actions, simulator world commands (or on-table cues for a physical build) and self-checks shown as ✓/✗ with evidence.
Catalogue: `ambulance_demo` (9 steps, 8 checks) and four failure drills. Actions run as the starting operator and are audited. `app/provenance.py` builds the live
"built vs simulated" table from what each data path reports (`origin` / `source`).

## 8. Reliability, security and failure handling
| Failure | Detection | Behaviour |
|---|---|---|
| Backend unreachable | firmware: no successful exchange for 5 s | fixed-time cycle, always yellow + all-red; resumes on contact |
| Camera feed lost | no readings for 5 s | marked stale, never zero; controller holds; recovers automatically |
| Controller link lost | no device report for 5 s | dashboard flags stale; public view "unknown" |
| EV location lost | no position for 4 s | warnings → `cancelled_stale`, priority withdrawn; resumes when seen |
| LLM down / slow / invalid output | timeout, error, malformed reply | labelled deterministic fallback; control unaffected |
| Spoofed or replayed device request | bad HMAC, reused boot id, non-increasing seq, unknown id, wrong role | rejected (401/403) and audit-logged |
| Conflicting lamps reported | validator on `/state` | critical system event; firmware itself cannot produce them |
| Session / report flood from phones | per-address and per-session limits, global session cap (200) | 429 / 503, nothing else affected |
| Spoofed or junk road-camera upload | role `road_camera` + HMAC, ≤ 400 KB, JPEG magic + end marker, server-generated filename | 422 and audit; detections are only unreviewed reports |
| Public hazard spam | one pending-review cap per source (25), rate limits | queue full message; no effect on routing until verified |
| Operator needs the last run again | every scenario run is recorded second by second | Replay tab (demo insurance) |
Secrets live in `.env` (never committed, never logged or returned by the API; the LLM key is excluded from `repr`). Data retention (scheduled every 5 min and on admin request; the audit log is never purged): raw traffic snapshots 24 h, EV positions 6 h, decisions and events 7 days, device commands 24 h, expired sessions, road-camera images 24 h, only the 5 latest scenario recordings. Road-user sessions never touch the database. The public API never exposes ids, routes or coordinates of emergency vehicles. What leaves the laptop when the LLM is enabled is documented in the README.

## 9. Testing
Automated tests cover: state machine and validator, controller rules (incl. starvation and gridlock), routing (vs brute force), EV lifecycle and corridor,
incidents, hazards (verification workflow, penalties, secure uploads, detector), road-user relevance matrix and alert lifecycle, auth and the viewer/operator/admin
matrix (every denial audited), admin export / retention / health / metrics / request guards, LLM safety, firmware core (C++ vs Python golden traces, signatures, parsing,
595 chain), vision pipeline (vs synthetic ground truth) and vision-in-the-loop, the full scenarios and drills (10 of 10 random seeds clean), the 12-intersection city,
and the preflight tool. `python -m simulator.evaluate` reproduces the measured A/B results in `docs/RESULTS.md`.
Firmware **compiles** for the real targets (`esp32dev`, `lamptest`, `esp32cam`); it has not been flashed.

## 10. Known gaps
No real-hardware or real-camera verification (firmware compiled, not flashed). The road-camera pothole finder is a heuristic that needs an operator to verify.
Measured benefits are **simulated** (see `docs/RESULTS.md` for what the model does and does not support). Road users drive virtual cars (dead reckoning), phones do not report GPS.
Operator alerts are on-screen only (no push/sound). Default passwords/keys are development values (the health check says so). LAN HTTP, no TLS.
