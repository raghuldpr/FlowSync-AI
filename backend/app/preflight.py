"""Pre-demo checklist as one command.

   python -m app.preflight --url http://LAPTOP_IP:8000 [--password ...] [--drill drill_llm_down]

Checks the RUNNING system the way a judge would experience it: reachable, can log in, devices alive, control loop alive,
what is built vs simulated right now, scenario catalogue, optional live drill. Exit code 1 if anything FAILS.
"""
import argparse
import os
import socket
import sys
import time

import httpx

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


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


def run(client, password: str, drill: str = None, wait_s: float = 90.0, sleep=time.sleep) -> list:
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
        add({"ok": PASS, "warn": WARN, "fail": FAIL}[c["status"]], c["name"], c["detail"])

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
    add(PASS if llm["enabled"] else WARN, "LLM", f"{llm['model']} ({llm['provider_host']})" if llm["enabled"] else "not configured: the labelled deterministic fallback will answer")

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


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--password", default=os.getenv("FLOWSYNC_OPERATOR_PASSWORD", "operator-dev"))
    p.add_argument("--drill", help="also run this scenario/drill and require its self-checks to pass (e.g. drill_llm_down)")
    a = p.parse_args(argv)
    with httpx.Client(base_url=a.url, timeout=5.0) as c:
        results = run(c, a.password, a.drill)
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
