# Demo day checklist

One page. Tick it from top to bottom. Details: `docs/NETWORK_SETUP.md` (hotspot), `docs/REHEARSAL.md` (bots), `docs/DEMO_SCRIPT.md` (what to say), `docs/LLM_SETUP.md` (LLM), `docs/HARDWARE_RUNBOOK.md` (boards).

Commands run in Windows PowerShell from the repository root.

## Before leaving home

- [ ] Fresh private credentials (writes only git-ignored files, prints nothing secret): `python -m app.setup_demo`. Read the operator login in `.env` (`FLOWSYNC_OPERATOR_PASSWORD`). Re-run with `--force` to replace them.
- [ ] LLM: put your own `LLM_ENABLED`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` in `.env` (`docs/LLM_SETUP.md`). **The real provider has not been tested yet**: ask for one answer on the Assistant panel, then turn the internet off and run the `drill_llm_down` drill.
- [ ] Optional extras installed: `pip install -r requirements-demo.txt` (QR sheet), `pip install -r requirements-sumo.txt` (SUMO evidence view), `python -m app.vision.fetch_model` (pothole model; without it the labelled heuristic is used).
- [ ] Web big screen: `cd web ; npm install ; npm run build` works.
- [ ] Start everything once, as you will on the day: `.\scripts\start_demo.ps1 -Web -Bots`. Then `python -m app.preflight --demo` must end `READY` (see below for what the warnings mean).
- [ ] Print the QR sheet (`data/qr/join.html`, written by `app.joinqr`). The address changes when the hotspot restarts, so you will reprint or re-show it at the venue.
- [ ] Charge everything. Pack: laptop charger, USB cables, the router (backup), spare jumpers and a spare ESP32, printed QR sheet, a phone with data for USB tethering.

## At the venue

- [ ] Hotspot on, band **2.4 GHz**, phone USB-tethered for internet. Check `ipconfig`: the laptop should be `192.168.137.1` (preflight `--demo` warns if not).
- [ ] Firewall rule for TCP 8000 allowed once (admin PowerShell, command in `docs/NETWORK_SETUP.md`).
- [ ] `.\scripts\start_demo.ps1 -Web` then, in a second window, `python -m app.preflight --demo`. Fix every **FAIL**. Read every **WARN** and decide.
- [ ] Rerun `python -m app.joinqr` and re-show the QR sheet if the address changed.
- [ ] Join from one real phone and from one more device. Windows Mobile Hotspot is believed to allow about 8 clients: count your boards plus phones against that and use the router if it is tight.
- [ ] Rehearse with the bots (`-Bots`, or `python -m simulator.guest_bots`) and with real phones. Bots are simulated guests: they prove nothing about Wi-Fi or browsers.
- [ ] Browser tabs preloaded: `/presenter.html`, `/operator.html` (signed in), the big screen at `http://localhost:5173`, and the Replay tab ready.

## 2 hours before judging: freeze

- [ ] No new features. Only bug fixes.
- [ ] One full run of the demo from the top, including the ambulance and the V2V alert (labelled **SIMULATED**).
- [ ] `python -m app.preflight --demo --drill drill_llm_down` ends `READY`.
- [ ] Record or note a good run so the Replay tab has a clean one.

## If something fails

| Failure | Do |
|---|---|
| Hotspot drops or the address changes | Toggle it back on, check `ipconfig`, rerun `python -m app.joinqr`, re-show the QR. If it keeps failing, switch to the router and rerun `app.joinqr --host <laptop address>`. |
| Too many devices on the hotspot | Router backup. Judges join in turns; the bots can stand in for the rest in the big-screen view. |
| A phone will not load the page | Check it is on the same Wi-Fi (not mobile data), and open the address from the QR sheet. Try another phone. |
| LLM is slow or down | Nothing to fix: the Assistant answers from the **labelled deterministic fallback**. Say so. |
| Live run misbehaves | Operator page > **Replay** tab > play the last good run, and say it is a recording. |
| Backend died | `.\scripts\stop_demo.ps1`, then `.\scripts\start_demo.ps1`. Logs are in `data\logs`. |
| Credentials warning from preflight | Run `python -m app.setup_demo`, restart the backend. |

## Reading `app.preflight --demo`

| Line | Meaning |
|---|---|
| `credentials` FAIL | Public development passwords or keys are in use. Only `--demo` makes this a failure. |
| `hotspot address` WARN | `192.168.137.1` is not on this machine: hotspot is off, or you are on the router backup. |
| `backend on the LAN` FAIL | Phones cannot reach the backend: start it on `0.0.0.0` (the script does) and open the firewall. |
| `hotspot band (reminder)` WARN | Always shown: a reminder to lock 2.4 GHz. Not a fault. |
| `LLM` WARN | No provider configured; the labelled fallback answers. |
| `SUMO` / `pothole model` WARN | Optional extras not installed. The demo still runs. |

## Hardware

**To be filled in after the bench tests.** None of the firmware has run on a board yet. When it has, record here: which boards and cables worked, the pin map as built, the upload method that fixed the car's flash error, and the order to power things on stage. Until then the hardware prototypes are described as "compiles and host-tested, not run on a board". The tabletop, car and ESP32-CAM are standalone: none needs the laptop or the hotspot.
