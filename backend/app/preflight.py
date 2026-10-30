"""Pre-demo checklist as one command.

   python -m app.preflight --url http://LAPTOP_IP:8000 [--password ...] [--drill drill_llm_down] [--demo]

--demo is the strict mode for demo day: development credentials FAIL (not warn), and this machine is checked too (hotspot
address, backend reachable on the LAN, SUMO and the pothole model as optional extras). The operator password is read from
FLOWSYNC_OPERATOR_PASSWORD or the git-ignored .env (see python -m app.setup_demo); it is never printed.

Checks the RUNNING system the way a judge would experience it: reachable, can log in, devices alive, control loop alive,
what is built vs simulated right now, scenario catalogue, optional live drill. Exit code 1 if anything FAILS.
"""
import argparse
import os
import socket
import sys
import time

import httpx

from app import config as _config  # noqa: F401  (importing it loads .env, so the generated operator password is found)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
HOTSPOT_ADDRESS = "192.168.137.1"        # the laptop's address when Windows Mobile Hotspot is on


def lan_addresses() -> list:
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                out.add(ip)
    except OSError:
        pass
    return sorted(out)


def run(client, password: str, drill: str = None, wait_s: float = 90.0, sleep=time.sleep, demo: bool = False) -> list:
    """client: httpx-style client with base_url set (a real httpx.Client or a FastAPI TestClient). Returns [(status, check, detail)]."""
    res = []
    add = lambda s, c, d: res.append((s, c, d))
    try:
        r = client.get("/api/health")
        add(PASS if r.status_code == 200 else FAIL, "backend reachable", f"HTTP {r.status_code}")
    except Exception as e:
        add(FAIL, "backend reachable", type(e).__name__)
        return res
    r = client.post("/api/auth/login", json={"username": "operator", "password": password})
    if r.status_code != 200:
        add(FAIL, "operator login", f"HTTP {r.status_code} (wrong password? FLOWSYNC_OPERATOR_PASSWORD)")
        return res
    add(PASS, "operator login", "ok")
    h = {"Authorization": "Bearer " + r.json()["token"]}

    health = client.get("/api/operator/health", headers=h).json()
    for c in health["checks"]:
        status = {"ok": PASS, "warn": WARN, "fail": FAIL}[c["status"]]
        if demo and c["name"] == "credentials" and status == WARN:
            status = FAIL                # an open demo must not run on the published development passwords and keys
            c = dict(c, detail=c["detail"] + " -> run: python -m app.setup_demo")
        add(status, c["name"], c["detail"])

    prov = client.get("/api/operator/provenance", headers=h).json()
    for p in prov:
        if p["status"] == "Not connected":
            add(WARN, f"data path: {p['component']}", "not connected")
    summary = ", ".join(f"{p['component'].split(' (')[0]}={p['status']}" for p in prov[:4])
    add(PASS, "built vs simulated (live)", summary)

    topo = client.get("/api/public/topology").json()
    add(PASS, "topology", f"{sum(1 for n in topo['nodes'].values() if n['kind'] == 'intersection')} intersections, {len(topo['edges'])} roads")
    scn = client.get("/api/operator/scenarios", headers=h).json()
    add(PASS if scn else FAIL, "scenarios available", ", ".join(s["id"] for s in scn))
    llm = client.get("/api/llm/status", headers=h).json()
    add(PASS if llm["enabled"] else WARN, "LLM", f"provider {llm['model']} ({llm['provider_host']}); not tested here, run the drill" if llm["enabled"] else "FALLBACK: not configured, the labelled deterministic answers are used")

    ov = client.get("/api/operator/overview", headers=h).json()
    stale = [i for i, s in ov["signals"].items() if s["reported_stale"]]
    add(PASS if not stale else WARN, "intersection reports fresh", "all fresh" if not stale else "stale: " + ", ".join(stale))

    if drill:
        r = client.post("/api/operator/scenario/start", json={"id": drill}, headers=h)
        if r.status_code != 200:
            add(FAIL, f"drill {drill}", f"could not start: HTTP {r.status_code} {r.text[:80]}")
        else:
            t0 = time.monotonic()
            st = r.json()
            while st["state"] == "running" and time.monotonic() - t0 < wait_s:
                sleep(1.0)
                st = client.get("/api/operator/scenario", headers=h).json()
            checks = [s["expect"] for s in st["steps"] if s["expect"]]
            bad = [c["desc"] for c in checks if c["ok"] is not True]
            if st["state"] == "finished" and not bad:
                add(PASS, f"drill {drill}", f"{len(checks)}/{len(checks)} self-checks passed")
            else:
                add(FAIL, f"drill {drill}", f"state={st['state']}; not passed: {bad or 'timed out'}")
    return res


def _can_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def local_checks(port: int = 8000, ips: list = None, connect=_can_connect, have_module=None, model_installed=None) -> list:
    """Checks of THIS machine for demo day. Pure of side effects apart from one TCP connect; every input can be injected."""
    import importlib.util
    res = []
    add = lambda s, c, d: res.append((s, c, d))
    ips = lan_addresses() if ips is None else ips
    if HOTSPOT_ADDRESS in ips:
        add(PASS, "hotspot address", f"{HOTSPOT_ADDRESS} is on this machine")
    else:
        add(WARN, "hotspot address", f"{HOTSPOT_ADDRESS} not found (addresses: {', '.join(ips) or 'none'}). Turn Mobile Hotspot on, or use the router backup")
    target = HOTSPOT_ADDRESS if HOTSPOT_ADDRESS in ips else next(iter(ips), None)
    if target is None:
        add(FAIL, f"backend on the LAN (port {port})", "no LAN address: phones and boards cannot reach this machine")
    elif connect(target, port):
        add(PASS, f"backend on the LAN (port {port})", f"reachable at {target}")
    else:
        add(FAIL, f"backend on the LAN (port {port})", f"not reachable at {target}: start it with --host 0.0.0.0 and allow TCP {port} in the firewall (docs/NETWORK_SETUP.md)")
    add(WARN, "hotspot band (reminder)", "lock Mobile Hotspot to 2.4 GHz: ESP32 boards cannot join 5 GHz; Windows allows about 8 clients")
    have_module = have_module or (lambda m: importlib.util.find_spec(m) is not None)
    add(PASS if have_module("traci") and have_module("sumolib") else WARN, "SUMO (optional)",
        "traci and sumolib installed" if have_module("traci") and have_module("sumolib") else "not installed: the evidence view is unavailable (pip install -r requirements-sumo.txt)")
    if model_installed is None:
        try:
            from app.vision import pothole_model
            model_installed = pothole_model.status()["installed"]
        except Exception:
            model_installed = False
    add(PASS if model_installed else WARN, "pothole model (optional)",
        "weights installed" if model_installed else "not installed: the labelled heuristic is used (python -m app.vision.fetch_model)")
    return res


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--password", default=os.getenv("FLOWSYNC_OPERATOR_PASSWORD", "operator-dev"))
    p.add_argument("--demo", action="store_true", help="strict demo-day mode: development credentials fail, and this machine is checked too")
    p.add_argument("--drill", help="also run this scenario/drill and require its self-checks to pass (e.g. drill_llm_down)")
    a = p.parse_args(argv)
    with httpx.Client(base_url=a.url, timeout=5.0) as c:
        results = run(c, a.password, a.drill, demo=a.demo)
    if a.demo:
        from urllib.parse import urlparse
        results += local_checks(urlparse(a.url).port or 8000)
    width = max(len(r[1]) for r in results)
    for status, name, detail in results:
        print(f"[{status}] {name.ljust(width)}  {detail}")
    ips = lan_addresses()
    if ips:
        print(f"\nThis machine's LAN address(es): {', '.join(ips)}  -> use as BACKEND_HOST in firmware config.h; phones open http://{ips[0]}:8000/driver.html")
    fails = sum(1 for r in results if r[0] == FAIL)
    print(f"\n{'READY' if not fails else 'NOT READY'}: {sum(r[0] == PASS for r in results)} pass, {sum(r[0] == WARN for r in results)} warn, {fails} fail")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
