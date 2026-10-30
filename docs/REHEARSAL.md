# Rehearsal

Rehearse the whole demo without 6 phones by using **guest bots**. They stand in for judges' phones and use only the public guest API (no login, no form). Everything V2V is **simulated** by the backend, and so are the bots: they are not real phones and prove nothing about Wi-Fi, browsers or hotspots.

## 1. Run it (four terminals)

Windows PowerShell, from the repository root. Each Python terminal needs `$env:PYTHONPATH = "backend"` (the bots also need the repository root as the working directory).

```powershell
# 1. backend (all interfaces, so phones can reach it)
.\.venv\Scripts\python -m uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000

# 2. a checked run of the bots: joins guests + an ambulance, prints a PASS/FAIL table
.\.venv\Scripts\python -m simulator.guest_bots --vehicles 5 --ambulance 1

# 3. big-screen rehearsal: the same run again and again, slowly (Ctrl+C to stop)
.\.venv\Scripts\python -m simulator.guest_bots --vehicles 5 --ambulance 1 --loop

# 4. the big screen (see web/): open it, sign in as the operator, open the 3D map
cd web ; npm run dev
```

Other screens to open while the bots run: `http://localhost:8000/presenter.html` and `http://localhost:8000/operator.html`.

Use `--base http://<laptop address>:8000` to point the bots at another machine, and `--seed N` to repeat the same trips.

## 2. What a bot run checks

| Check | What it proves |
|---|---|
| Vehicles join as guests | No login or form is needed; every guest is role `vehicle` |
| A vehicle cannot raise an emergency alert | The role is enforced on the server (HTTP 403) |
| Unknown roles are refused | HTTP 422 |
| Ambulances join and start a trip | Guest ambulances work with no operator |
| Ambulance view says SIMULATED | The V2V alert is labelled as simulated |
| Ambulance raises a V2V alert, then a second one at once | First is accepted, the second is rate-limited (HTTP 429) |
| Ambulance trips finish and give a receipt | Trip time and the "deleted" privacy note |
| Vehicles on the ambulance's route were warned | Server-side relevance reaches the cars it comes up behind |
| Warned vehicles see "has passed" or finish first | The warning is cancelled when the ambulance passes |
| No live emergency alert on a driving car at the end | Warnings clear up |
| Warnings use fixed wording and never say hurry | Fixed lawful templates |
| Sessions are really deleted | After leaving, the token returns 404 |

Two lines are informational and never fail a run: **route-change messages** (they need a blocked road to trigger, and the public API cannot create one) and the cars **elsewhere** (they may or may not be warned, depending on timing).

Three groups of vehicles are created so the table is easy to read: *on-route* (same road as the ambulance, must be warned), *turns-off* (shares the first road, then turns away, so it should see "has passed") and *elsewhere*.

The exit code is `0` when every check passes, `1` when any fails and `2` when the backend cannot be reached. Tokens are never printed; bots are shown by their pseudonym, such as `Car 3`.

## 3. Limits to know

* The backend allows **10 joins per minute per address** and **3 guest ambulances at once**, so use at most 10 bots in total per run. `--loop` waits 65 s between rounds for that reason.
* A run takes about 40 s of real time, because cars drive at demo pace.
* A car that has just arrived keeps its last alert on screen until it is deleted about 8 s later. The bots skip arrived cars when checking for leftover alerts.
* Bots do not test the QR page, audio, full-screen takeover, vibration or the phone's browser. Test those on real phones (below).

## 4. Checklist for 6 or more real phones

Do this once with real devices, ideally the day before and again on the day.

- [ ] Hotspot is on, set to 2.4 GHz, the phone providing it is plugged in, and `ipconfig` shows the laptop address (usually `192.168.137.1`).
- [ ] The firewall allows inbound TCP 8000 on all network profiles.
- [ ] `python -m app.joinqr` prints the current address and writes the QR sheet; the sheet shows the same address.
- [ ] Count devices: boards + laptop + phones. Windows Mobile Hotspot is believed to allow only about 8 clients; bring a router as backup.
- [ ] Every phone scans the QR, lands on the join page and gets a car in under 10 s.
- [ ] At least one phone joins as **ambulance**, picks a trip, and raises the alert. The cars ahead of it show the warning; a car elsewhere does not.
- [ ] Sound plays after the first tap ("Start driving" unlocks audio). iPhones do not vibrate.
- [ ] The big screen shows every phone's car, and the warned / not-warned view matches the phones.
- [ ] After arrival the receipt appears and the session is gone (reloading the page shows the join screen).
- [ ] The offline fallback works: unplug the internet and check that the demo still runs without the cloud LLM.
- [ ] Default credentials have been changed (the health check warns).
- [ ] Features are frozen 2 hours before judging; run the bots in `--loop` mode on the big screen as a final soak test.
