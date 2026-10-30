# Hosting on Zoho Catalyst (AppSail)

**Status: prepared, not deployed.** Nothing here has run on Catalyst yet. The code and the build were checked on a Windows PC only (see "What is verified"). You do the Catalyst login and the deploy yourself; no account, login or secret is stored in the repository.

**The laptop and the hotspot (`docs/NETWORK_SETUP.md`) stay the primary plan.** They work with no internet for everything except the LLM. Use the hosted copy when the hotspot cannot carry the judges' phones, or as a backup.

What hosting gives: one public HTTPS address that phones reach on their own mobile data, with no hotspot, no firewall rule and no 8-client limit. The hardware stays standalone and is not connected to it.
What it costs: the demo depends on the internet and on Catalyst, in-memory state is lost when the instance restarts, and the backend must stay a single instance (see "Constraints").

## What is verified from the official docs, and what is not

Sources: [AppSail basics](https://docs.catalyst.zoho.com/en/serverless/help/appsail/appsail-basics/), [AppSail configurations](https://docs.catalyst.zoho.com/en/serverless/help/appsail/appsail-configurations/), [Initialize AppSail (CLI)](https://docs.catalyst.zoho.com/en/cli/v1/initialize-resources/initialize-appsail/), [CLI command reference](https://docs.catalyst.zoho.com/en/cli/v1/cli-command-reference/), [free tier](https://docs.catalyst.zoho.com/en/deployment-and-billing/billing/free-tier/). Checked on 2026-10-31.

| Verified (stated in the docs) | Not stated in the docs: assumed or to check after deploy |
|---|---|
| Managed Python runtimes 3.10, 3.11, 3.12, 3.13; a custom runtime takes a Linux AMD64 container image | Whether the instance count can be **pinned to one** (the docs show no setting) |
| The app reads its port from `X_ZOHO_CATALYST_LISTEN_PORT` (default 9000), must listen within 10 s, binds `0.0.0.0` | Whether the filesystem is ephemeral, and whether `/tmp` is writable (only "disk default 256 MB" is stated) |
| **Startup commands run without a shell**; use `sh -c '...'` to expand variables | The exact Python stack name in the init prompt (the docs show only `Python_3_9`) |
| `app-config.json` keys: `command`, `memory` (default 512 MB), `env_variables`; console values override the file | The full `app-config.json` schema and where `init` writes it |
| Environment variables: set in the console (or the file for CLI apps); encrypted; **separate development and production values**; production values need a production deploy first | WebSocket support (this app does not use WebSockets) and request body size limits |
| Max **5 instances per app**; **100 concurrent requests per instance**; a new instance starts at 80 % of that (80 concurrent); idle instances scale down after 5 minutes | Whether the CPU keeps running between requests (the app runs a background control loop) |
| Request timeout **30 s**; a cold start happens on the first request after idle | The CLI install command (the CLI page points to a separate install page; `npm install -g zcatalyst-cli` is from general knowledge: check it) |
| URLs: `https://<service>-<ZAID>.development.catalystappsail.com` (development) and `https://<service>-<ZAID>.catalystappsail.com` (production) | How many proxy hops sit in front of the app (decides `FLOWSYNC_TRUST_PROXY_HOPS`; check with `/api/whoami`) |
| CLI commands `login`, `init`, `appsail:add`, `deploy`, `serve` exist | The free-tier numbers below were taken from the free-tier page through a search summary: confirm them in your console |

## Constraints that shape the setup

1. **One instance only.** Live state (guest sessions, emergency-vehicle state, traffic, rate limits) is in memory. A second instance would hold a different half of the world. AppSail starts another instance only when one instance has about 80 requests in flight at once; ten or twenty phones polling once a second keep that near zero, so it should not happen at demo scale. The docs offer no setting to forbid it, so **check it**: `/api/whoami` returns an `instance` id that is new for every process start. If it ever changes between your checks, the app restarted or scaled and everyone must join again.
2. **Restarts lose state.** Idle instances are scaled down after 5 minutes and the next request is a cold start. Keep it warm (below) and open the pages a few minutes before judging.
3. **The database is not durable.** The command sets `FLOWSYNC_DATA_DIR=/tmp/flowsync`. On a restart or a deploy you lose the audit log (and its chain), hazards, authority reports and assistant traces; the logins are re-created from the environment variables. Guest sessions and the emergency-vehicle state were never stored. The app starts cleanly from an empty folder.
4. **No pothole weights and no OpenCV on the host.** The pothole layer on phones is simulated (operator-declared or scripted hazards) and works. Camera-image upload would fail on the host, and the boards are standalone, so nothing uses it.
5. **The 30 s request limit.** The assistant waits up to `LLM_DEADLINE_S` (default 25 s). Set it to 20 on the host.
6. **The development device keys are public** (they are in the repository). The build refuses to run without your private keys from `python -m app.setup_demo`. These keys are used only by the **simulator**, which posts to the hosted backend through the signed device API; no board uses them. `config/devices.yaml` itself is not changed.
7. **The app refuses to start with development credentials on a public host.** If `FLOWSYNC_PUBLIC_URL` is set, or the app runs on Catalyst (it sees `X_ZOHO_CATALYST_LISTEN_PORT`), and any account password is still `operator-dev`, `viewer-dev` or `admin-dev`, or any device key still contains `change-me`, startup stops with a message that names the accounts (never the values). So the first start fails until the three passwords exist as environment variables (step 7). That is intended: it keeps the public copy from running with the public development passwords.

## Settings the code now understands

| Variable | Meaning | Default |
|---|---|---|
| `FLOWSYNC_DATA_DIR` | Folder for the database and images; created if missing. `FLOWSYNC_DB_PATH` and `FLOWSYNC_IMAGE_DIR` still win | repository paths |
| `FLOWSYNC_PUBLIC_URL` | Public address used by `python -m app.joinqr` for the QR codes | none (hotspot address) |
| `FLOWSYNC_TRUST_PROXY_HOPS` | Number of trusted proxies in front of the app. The client address becomes that entry counted from the **right** of `X-Forwarded-For` (the left side can be forged). `0` ignores the header | `0` |
| `FLOWSYNC_JOIN_PER_MIN`, `FLOWSYNC_JOIN_AMBULANCE_PER_MIN` | Guest joins and ambulance trips per client address per minute | `10`, `3` |
| `LLM_TIMEOUT_S`, `LLM_DEADLINE_S` | Per-call and total LLM time | `15`, `25` |

**Why the proxy setting matters.** Behind a proxy every request seems to come from the proxy, so all judges would share one address and the join limit (10 per minute) would block most of them. With `FLOWSYNC_TRUST_PROXY_HOPS=1` the limits count the real address. Judges on the same venue Wi-Fi still share one address (it is one address to the internet); if they are throttled, raise `FLOWSYNC_JOIN_PER_MIN` (for example `60`) rather than turning limits off. Hazard reports stay at 20 per address per minute. Leave `FLOWSYNC_TRUST_PROXY_HOPS` at `0` anywhere that is not behind a proxy you control: otherwise a visitor could invent their own address.

`/api/whoami` (public, read-only) shows the client address the server derived and the instance id. It tells you nothing else.

## Step by step (Windows PowerShell, from the repository root)

**1. Private secrets (once).**
```powershell
$env:PYTHONPATH = "backend"
python -m app.setup_demo
```
This writes `.env` (passwords) and `data\secrets\devices.demo.yaml` (device keys), both git-ignored. Read the passwords in `.env` yourself.

**2. Catalyst account and CLI.** Create a Catalyst project in the Zoho Catalyst console. Install the CLI by following the "Installing Catalyst CLI" page in the Catalyst docs (the CLI command reference points to it; the usual command is `npm install -g zcatalyst-cli`, from general knowledge, so check it), then log in:
```powershell
catalyst login
```

**3. Build the upload folder.**
```powershell
.\deploy\catalyst\build.ps1
```
It builds `deploy\catalyst\appsail\` (about 21 MB): Linux Python packages (Python 3.12), the app, `config\topology.yaml`, **your private** `devices.yaml`, and `dashboard\`. It does not copy `.env`. The folder holds your device keys, is git-ignored, and must never be committed or shared.

**4. Create the AppSail service.** Run this in `deploy\catalyst` (that folder becomes the Catalyst project directory):
```powershell
cd deploy\catalyst
catalyst init
```
Answer the prompts: pick your Catalyst project, then AppSail with the **Catalyst-Managed Runtime**, **N** for a sample project, source directory = the absolute path of `deploy\catalyst\appsail`, a service name, build path = the same folder, and the **Python 3.12** stack. The prompts are as the CLI docs describe them; the exact wording may differ.

**5. Set the startup command and memory.** `init` creates `app-config.json` for the service. Copy the `command` and `memory` values from `deploy\catalyst\app-config.example.json` into it. `build.ps1` keeps that file when you rebuild. Put **no secrets** in `app-config.json`: secrets go in the console (next step).

**6. Deploy.**
```powershell
catalyst deploy
```
Note the service URL in the console (development first). Replace `FLOWSYNC_PUBLIC_URL` in the next step with it.

**7. Environment variables, in the Catalyst console** (App Execution Settings or the service's Configuration). Values are per environment: set them for the environment you will use, and for production first do a production deploy.

> **Order matters because of the startup check.** The first deploy may report the service as not started until the three `FLOWSYNC_*_PASSWORD` variables exist. Either set them in the console and then redeploy/restart, or put them under `env_variables` in `deploy\catalystppsailpp-config.json` before the first `catalyst deploy` (that folder is git-ignored and already holds your device keys). Do not paste them anywhere else.

| Name | Value |
|---|---|
| `FLOWSYNC_VIEWER_PASSWORD`, `FLOWSYNC_OPERATOR_PASSWORD`, `FLOWSYNC_ADMIN_PASSWORD` | from your `.env` (never paste them in chat) |
| `FLOWSYNC_TRUST_PROXY_HOPS` | `1` (check in step 8) |
| `FLOWSYNC_PUBLIC_URL` | the service's public `https://...` address |
| `LLM_ENABLED`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | as in `docs/LLM_SETUP.md` (the key only here and in your `.env`) |
| `LLM_DEADLINE_S` | `20` |

Restart or redeploy the service after changing them.

**8. Check the hosted copy** (replace the address):
```powershell
curl https://YOUR-SERVICE.catalystappsail.com/api/health
curl https://YOUR-SERVICE.catalystappsail.com/api/whoami
```
- `/api/health` must answer `{"status":"ok"}`.
- `/api/whoami` must show **your own public address** as `client_address` (compare with an "what is my IP" site, from the same network). If it shows an address that is not yours, or the same address for different people, change `FLOWSYNC_TRUST_PROXY_HOPS` (try `2`) and redeploy.
- Repeat `/api/whoami` after ten idle minutes and later: the `instance` must stay the same while you are using it. A new value means a restart.
- Run the readiness check from your laptop. Do **not** add `--demo` (it checks the laptop's hotspot, which does not apply here):
```powershell
python -m app.preflight --url https://YOUR-SERVICE.catalystappsail.com --password <operator password from .env>
```
The `credentials` line must not warn about development defaults. Also confirm that the signal and traffic checks pass once the simulator is running (step 10); that also shows the background control loop is alive.

**9. Join links for the phones.**
```powershell
$env:PYTHONPATH = "backend"
python -m app.joinqr --url https://YOUR-SERVICE.catalystappsail.com
```
It writes `data\qr\join.html` (needs `pip install -r requirements-demo.txt`). The address never changes, so one printout is enough. Without `--url` it prints the hotspot addresses as before.

**10. Simulator, bots and screens against the hosted address.**
```powershell
python -m simulator.run_sim --url https://YOUR-SERVICE.catalystappsail.com --password <operator password> --demo
python -m simulator.guest_bots --base https://YOUR-SERVICE.catalystappsail.com --vehicles 5 --ambulance 1
```
The simulator signs its calls with the device key in your local `.env`/devices file, which is the same file that went into the build. The old pages (`/presenter.html`, `/operator.html`, `/driver.html`) are served by the hosted backend. The 3D big screen (`web/`) is configured to proxy to `localhost:8000`; pointing it at the hosted address needs a one-line change in `web/vite.config.ts` (a configurable proxy target), **which this preparation did not make**.

  To run the big screen against the hosted copy, set the API address before `npm run dev`: `$env:FLOWSYNC_API = "https://YOUR-SERVICE.catalystappsail.com"; cd web; npm run dev` (the dev server proxies `/api` there; without it, it uses `http://localhost:8000`).

**11. Keep it warm.** While you wait for judging, ping it once a minute from the laptop:
```powershell
while ($true) { curl.exe -s https://YOUR-SERVICE.catalystappsail.com/api/health | Out-Null; Start-Sleep 60 }
```
The big screen and the bots also count as traffic.

## Cost

Per the official free-tier page (confirm in your console): the free allowance for AppSail is **900 GB-minutes** a month (container time at about $0.0013 per GB-minute beyond it), and once an account passes the free allowance a **minimum of $5 per project per month** applies. At 512 MB, 900 GB-minutes is roughly 30 hours of running time. Stop or delete the service when you are done. There is also a trial offer; check the pricing page.

## If it goes wrong

- **Keep the laptop plan ready.** The hosted copy is separate: the hotspot, `docs/NETWORK_SETUP.md` and `scripts\start_demo.ps1` need none of it. Switching back means printing the hotspot QR sheet (`python -m app.joinqr`).
- **Redeploy** with `.\deploy\catalyst\build.ps1` then `catalyst deploy` (this resets all hosted state).
- **To roll back** delete the service in the console, or set the phones back to the laptop address.
- **Boards:** decided: the table, car and ESP32-CAM stay completely standalone and send nothing to the backend. Everything the hosted backend shows comes from simulation (guest phones, the rehearsal bots and the simulator). No board settings, firmware changes or TLS-on-ESP32 work are needed.

## What is verified here

- The new settings, the proxy handling, the join limits, `/api/whoami`, empty-folder startup and the QR links have automated tests (`backend/tests/test_hosting.py`).
- The build produces a 20.8 MB folder with Linux wheels (for example `pydantic_core` for CPython 3.12, x86-64 Linux). The app code, run from that folder's layout on a Windows PC, started, served `/driver.html`, accepted a guest join, and gave the right client address with a simulated `X-Forwarded-For`.
- **Not verified:** anything on Catalyst itself: the deploy, the startup command with `sh -c`, `/tmp` being writable, the proxy hop count, instance behaviour when idle, the background loop running between requests, and the Linux packages loading on the host.
