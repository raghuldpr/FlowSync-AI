# Guest, ambulance and V2V API

Everything about V2V here is **simulated** by the backend. Guests are anonymous: no name, no account. A guest holds a random `token` (returned once on join) that only works for their own session. Base path `/api/public/road-users` needs no login; `/api/operator/...` needs a login (`viewer` or higher unless noted).

## Roles
| Role | How you get it | May do |
|---|---|---|
| `vehicle` (guest) | `POST /api/public/road-users` with no role or `"vehicle"` | Drive, receive alerts, accept a suggested route, report a hazard, leave |
| `ambulance` (guest) | `POST /api/public/road-users` with `"role": "ambulance"` | Everything a vehicle can, plus start an ambulance trip (signal priority, rerouting) and raise a simulated V2V emergency alert |
| `viewer` / `operator` / `admin` | Login (`/api/auth/login`) | Unchanged. The road-user feed needs `viewer` or higher. |

Roles are checked on the server. Any other `role` value returns 422.

## Endpoints

### `POST /api/public/road-users` (public)
Request (all fields optional):
```json
{ "role": "vehicle", "origin": "ext_W", "destination": "ext_S" }
```
- `role`: `"vehicle"` (default) or `"ambulance"`.
- `origin` / `destination`: external node ids (`ext_W`, `ext_E`, `ext_S`). A vehicle with none gets a random trip (a QR gate link passes `origin`). An ambulance with none uses the topology's default trip.
- Errors: 422 (bad role, nodes or route), 429 (10 joins/min per address; 3 ambulance joins/min), 503 (demo full, or 3 guest ambulances already running).

Response 200 (the view below plus the token):
```json
{ "token": "…", "id": "Car 3", "role": "vehicle", "color": "red", "plate": "FS-003", "status": "driving",
  "edge": "W1", "progress": 0.0, "from": "the West", "to": "the South",
  "route_text": "the West → Intersection 1 → Intersection 3 → the South",
  "alerts": [], "suggestion": null, "version": 0 }
```
An ambulance has `"id": "Ambulance 4"` and an extra `ambulance` object (see view). Starting an ambulance trip creates a real emergency-vehicle session (`source: guest`), so it gets the same signal priority and rerouting as an operator-declared one.

### View: `GET /api/public/road-users/{token}`  and  `GET …/{token}/poll?after=<version>&timeout=<s ≤ 25>`
Only the owner can read it. `poll` long-polls and returns as soon as `version` changes. 404 means unknown or expired (the session is deleted).
```json
{ "id": "…", "role": "vehicle|ambulance", "color": "red", "plate": "FS-003", "status": "driving|arrived",
  "edge": "L13", "progress": 0.42, "from": "…", "to": "…", "route_text": "…", "version": 7,
  "alerts": [ { "id": "ev:5", "kind": "ev|v2v|incident|hazard|route", "state": "active|updated|redirected|cancelled_passed|cancelled_route_change|cancelled_stale|expired|cleared",
                "text": "fixed wording", "band": "soon|imminent|null",
                "place": "L13",          // only on kind incident/hazard: the road to mark on the map
                "simulated": true } ],    // only on kind v2v
  "suggestion": { "text": "…", "saves_s": 9 } ,
  "ambulance": { "simulated": true, "can_alert": true, "alert_cooldown_s": 0, "alert_live": false,
                 "eta_s": 31, "rerouted": false, "reroutes": 0, "priority_at": ["Intersection 1"] },   // ambulance role only
  "receipt":   { "trip_s": 39, "from": "…", "to": "…", "made_way_for": 1, "privacy": "…" } }          // once status is "arrived"
```
Live states are `active`, `updated`, `redirected`. Every other state is a terminal "all clear" that stays visible a few seconds. Alert text is always from the fixed templates (`backend/app/core/ev_warnings.py`); it never tells a driver to hurry. An ambulance is never warned about itself.

### `POST /api/public/road-users/{token}/alert` (guest, **ambulance only**)
No body. Raises a **simulated** V2V emergency alert, delivered by the backend to cars on the ambulance's route ahead of it within 30 s.
```json
{ "ok": true, "simulated": true, "reached": 2, "cooldown_s": 15 }
```
Errors: 403 not an ambulance (audit-logged as `denied_role`), 404 unknown token, 409 no active trip, 429 cooldown (15 s) or more than 6 per minute. The alert stays live for 30 s or until the ambulance has passed or finished.

### `POST /api/public/road-users/{token}/route/accept` (guest)
Accepts the suggested route. 409 if there is none. Returns the view. (Not available as a concept for ambulances: they are routed by the emergency-vehicle manager.)

### `DELETE /api/public/road-users/{token}` (guest)
Leaves and deletes the session now. An ambulance's emergency session ends (`guest_left`). Returns `{"ok": true}`. Arrived sessions are also deleted automatically 8 s after arrival; idle ones after 10 minutes.

### `GET /api/operator/road-users` (viewer+)
List of every road user (guests and scripted ones):
```json
[ { "id": "Car 3", "role": "vehicle", "color": "red", "plate": "FS-003", "simulated": false, "status": "driving",
    "edge": "W1", "progress": 0.2, "warned": true, "alerts": { "ev:5": "active" } } ]
```
`simulated: true` marks scripted scenario cars (not real phones).

### `GET /api/operator/road-users/feed` (viewer+) — the big-screen feed
```json
{ "simulated_v2v": true, "total": 5, "warned": 2,
  "users": [ { "id": "Car 3", "role": "vehicle", "color": "red", "plate": "FS-003", "simulated": false,
               "status": "driving", "edge": "W1", "progress": 0.2, "warned": true, "alerts": { "ev:5": "active", "v2v:5": "active" } } ],
  "ambulances": [ { "id": "Ambulance 4", "role": "ambulance", "color": "blue", "plate": "FS-004", "edge": "W1", "progress": 0.05, "warned": false, "…": "…" } ],
  "v2v": [ { "t": 123.4, "from": "Ambulance 4", "heard": 2, "drivers": 4 } ] }
```
- Place a car on the map from `edge` (a topology edge id, e.g. `W1`, `L13`, `O3`) and `progress` (0–1 along it). Colour is a CSS colour name; an ambulance should be drawn red/blue regardless.
- `warned` is true while the car has a live emergency-vehicle or V2V alert. Show "warned" and "not warned" from it.
- `v2v` is the last 10 raised alerts (`heard` = cars that received it, `drivers` = other cars driving at the time).
- No tokens or positions beyond `edge`/`progress`. Poll about once a second (the existing `web/` pattern).

### `POST /api/public/hazards` (guest, unchanged) and hazard alerts
Hazards still need an operator to verify them. Verified hazards on a guest's route within 45 s come to phones as `kind: "hazard"` alerts with `place`.

### `POST /api/device/images` (device role `road_camera`, unchanged request)
Not used in the demo (the ESP32-CAM is standalone). If used, the pothole detector runs and creates an unreviewed hazard report whose `source` is now `camera_model` or `camera_heuristic` (was always `camera_heuristic`).

## Pothole model
- Source: <https://huggingface.co/vinothvikas1987/pothole-detection-yolov8>, file `best.onnx` (about 45 MB), YOLOv8s fine-tuned on the Road Damage Dataset.
- Licence: Apache-2.0, as stated on the model card (re-check before redistributing).
- SHA-256: `590a20e8c4a7bcbdb32e8a3b7b3a3c1d57d1fa8a7dde5c83fcec8928a4aa753f` (44,752,195 bytes), the same value the model page shows. It is pinned in `backend/app/vision/fetch_model.py`; `python -m app.vision.fetch_model` downloads to `models/pothole_yolov8s_rdd.onnx` (git-ignored) and deletes any file whose hash differs.
- Output checked: the model loads with OpenCV DNN and returns shape (1, 9, 8400), as the decoder expects. Detection quality on real ESP32-CAM photos is **not yet measured**.
- Until the weights are installed, the heuristic runs and detections are labelled `camera_heuristic`.
- Offline use: `python -m app.vision.detect_images photos/*.jpg --out data/pothole_out`. See `docs/POTHOLE_MODEL.md`.

## Limits and rules
Alert wording is fixed. Only the ambulance role may raise a V2V alert. Per-ambulance cooldown 15 s, 6 alerts/min per session, at most 3 guest ambulances at once. Everything V2V is labelled simulated. Message types map to SAE J2735 in `docs/V2V_MESSAGES.md`.
