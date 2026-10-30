# Wow-factor tracker

Update the **Status** column as work lands. Status: `todo`, `doing`, `done`, `cut`. Impact and difficulty are estimates, not measurements.

Rules that every item must keep: warnings use fixed lawful templates (the LLM never writes them), the LLM never controls anything, advice never encourages hurrying, and what is simulated is labelled as simulated.

## Do first (hardware and housekeeping)

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | Commit all work; README links resolve | doing | Feature branches merged locally; nothing pushed |
| H2 | Bench-test every sensor on a breadboard (before the hackathon day) | todo | See `docs/HARDWARE_CAR_WIRING.md`, `firmware/car`, `firmware/camtest`, `lamptest` |
| H3 | Test hotspot: laptop reachable from a second phone and an ESP32; count devices | todo | `docs/NETWORK_SETUP.md`; `python -m app.joinqr` prints the address |
| H4 | Test the real LLM provider (Groq first) | todo | See `docs/LLM_SETUP.md` |
| H5 | Build the standalone table (2 ESP32 + 6 modules), car brain and ESP32-CAM; flash and bench-test each | todo | No camera calibration or ambulance marker any more; see `docs/HARDWARE_RUNBOOK.md` |
| H6 | Freeze features 2 hours before judging; rehearse with 6+ phones | todo | |

## Wow factors, in build order

| Order | Wow factor | Difficulty | Judge impact | Status | Done when |
|---|---|---|---|---|---|
| 1 | **Join by QR**: scan, get a car (colour, plate), tap "Start driving". No form. Per-gate QR codes. | Medium | Very high | done (guest role picker, `?gate=`) | A phone joins in under 10 s and gets a car |
| 2 | **Judges appear on the big screen** with name, colour and a ripple on join | Medium | Very high | doing (backend feed `/api/operator/road-users/feed` and presenter panel done; 3D map in `web/` pending) | Joining on a phone shows a car on the presenter map |
| 3 | **Selective warning wave**: full-screen takeover (red/blue glow, siren, direction arrow, ETA band, fixed text) only on relevant phones; big screen shows who was and was not warned | Medium | Very high | done (takeover screen; simulated V2V alert from a guest ambulance) | With 5 phones, only the ones on the corridor alert |
| 4 | **Live route change** on phones: "no longer approaching you", another phone lights up, then "ambulance has passed" | Easy to medium | High | done (toast with the fixed wording when an alert ends) | Triggered by the incident step in the scenario |
| 5 | **Trip receipt**: time, "you made way for 1 ambulance", "your session and location were deleted" | Easy | Medium | done | Shown at arrival; the data really is deleted |
| 6 | **Ghost ambulance**: shadow run with fixed-time signals and no warnings, side by side, live "seconds saved" | Medium | High | todo | Presenter shows both runs and the counter |
| 7 | **Hash-chained audit log** with an admin "verify chain" button | Easy | Medium | todo | Editing a row makes verification fail |
| 8 | **Visible LLM tool-call trace** (question, tool, result, proposal, validator verdict) | Medium | High | todo | Operator page shows the chain live |
| 9 | **Cockpit view**: mini-map, next signal countdown, queue ahead | Medium | High | todo | Uses real signal state |
| 10 | **Speed advice to arrive on green** (never above the limit, never "hurry for the amber") | Medium | Medium to high | todo | |
| 11 | **Early congestion alert** with an alternative route | Medium | Medium to high | todo | |
| 12 | **City pulse** on the big screen: flow particles, green-wave ribbon | Easy to medium | Medium | todo | |
| 13 | **Judge roles**: one drives the ambulance (dispatch QR), one is the operator, one reports a hazard photo | Medium | High | partly done: guests pick **vehicle** or **ambulance**; operator and hazard-photo roles are not built | Pick one or two |
| 14 | **Two emergency vehicles** with a deterministic priority rule (class, then ETA) | Medium to hard | Medium to high | todo, only if the above is solid | |
| 15 | **Physical sync**: table buzzer/LED fires with the phones | Medium | High | cut: the hardware prototypes are standalone and not connected to the software | |
| 16 | Speech-to-text for the operator question | Easy | Medium | todo | |
| 17 | Pre-translated warnings, vibration patterns (not on iPhone) | Easy | Low to medium | todo | |
| 18 | Mapping of our messages to SAE J2735 types (slide or doc, not code) | Easy | Low to medium | done (`docs/V2V_MESSAGES.md`) | |

## Cut on purpose

| Item | Why |
|---|---|
| V2V with ESP-NOW, RSU on the ESP8266, crash detection chain | Needs three more firmwares and untested backend work; a half-working chain looks worse than none. Present it as future work. V2V is shown by the **simulated** broadcast in the backend (`docs/V2V_MESSAGES.md`); the car firmware is a standalone sensor and alert unit with no Wi-Fi. |

## Demo-day gotchas

- Hotspot: 2.4 GHz only, keep the phone plugged in, check that devices can see each other, carry a backup.
- The laptop address changes when the hotspot restarts; rerun `python -m app.joinqr` and reprint or re-show the QR sheet.
- iPhones do not vibrate from web pages; sound plays only after a tap, so "Start driving" must unlock audio.
- Wake Lock and some browser features need HTTPS; skip them or use a local certificate. Avoid public tunnels.
- Keep the replay fallback ready.
