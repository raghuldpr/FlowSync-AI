# Simulated V2V messages and their SAE J2735 equivalents

Everything here is **simulated** by the backend. No radio (DSRC / C-V2X / ESP-NOW) is involved: phones post to the backend, the backend decides who is relevant, and each phone polls for its own alerts. The mapping below says what a real deployment would carry on air; we do not implement the J2735 encoding.

| FlowSync message | Raised by | Delivered to | Closest SAE J2735 message | Notes |
|---|---|---|---|---|
| Emergency vehicle approaching (`ev` alert) | The backend, from the ambulance's position and route | Road users the ambulance will come up behind within the 20 s warning window | **TIM** (Traveler Information) with an emergency-vehicle advisory; in the field also **EVA** (Emergency Vehicle Alert) | Infrastructure to vehicle (I2V); fixed wording |
| Emergency alert from a nearby ambulance (`v2v` alert, "Raise emergency alert") | A guest **ambulance** driver (role-checked on the server, one per 15 s) | Road users on the ambulance's route ahead of it within 30 s | **EVA** (Emergency Vehicle Alert), with the sender's **BSM** (Basic Safety Message) position implied | Vehicle to vehicle (V2V), simulated |
| Alert ended / vehicle has passed (`v2v_ended`, `ev_passed`, `ev_route_changed`) | The backend | Same users | **TIM** update or cancel | Cancellation is explicit so no stale warning stays on screen |
| Signal priority for the ambulance | The backend, per approach | The intersection controller | **SRM** (Signal Request Message) and **SSM** (Signal Status Message); **SPaT** for the resulting phase | Here the request is made by the backend itself, not on air |
| Road hazard / pothole (`hazard` alert) | Operator-verified report (camera detections start unverified) | Road users with the hazard on their route within 45 s | **TIM** road-condition advisory (ITIS codes) | Detection model output never reaches drivers without an operator check |
| Incident ahead (`incident` alert) | The backend's incident detector | Road users with it on their route | **TIM** | |
| Map of the road | `/api/public/topology` | Everyone | **MAP** (MapData) | |

## Rules that hold for every message
- Wording comes only from the fixed templates in `backend/app/core/ev_warnings.py`; the LLM never writes warnings.
- Wording never tells a driver to hurry, ignore a signal or block anyone ("make way only when it is safe and lawful to do so").
- A V2V alert never contains the sender's id, position or route, and no user can read another user's view.
- Only the `ambulance` role may raise an emergency alert. The check is on the server; every refusal is audit-logged.
- The V2V alert is rate-limited (15 s cooldown per ambulance, 6 per minute per session, 3 concurrent guest ambulances).
- Sessions are anonymous, kept in memory, and deleted a few seconds after arrival or after 10 minutes idle.
- Every view that shows a V2V alert labels it **simulated**.
