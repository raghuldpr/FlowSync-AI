"""Run the virtual tabletop against a live backend:  python -m simulator.run_sim [--url http://127.0.0.1:8000]

The simulator also listens for scenario WORLD commands, so the demo can be started from the dashboard's Scenario panel,
or from here:  --scenario ambulance_demo   (alias: --demo)
"""
import argparse
import time

import httpx

import simulator  # noqa: F401
from app.config import Settings, load_yaml
from app.core.topology import Topology
from simulator.tabletop import VirtualTabletop


def start_scenario(url: str, password: str, scenario: str):
    c = httpx.Client(base_url=url, timeout=5)
    token = c.post("/api/auth/login", json={"username": "operator", "password": password}).raise_for_status().json()["token"]
    r = c.post("/api/operator/scenario/start", json={"id": scenario}, headers={"Authorization": f"Bearer {token}"})
    print("scenario start:", r.status_code, (r.json().get("title") if r.status_code == 200 else r.text))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--speed", type=float, default=1.0, help="wall-clock speed factor for the loop sleep")
    p.add_argument("--heavy", default="", help="comma list of approaches with heavy arrivals, e.g. L12,W1")
    p.add_argument("--demo", action="store_true", help="start the 'ambulance_demo' scenario (needs the operator password)")
    p.add_argument("--scenario", help="start this scenario id instead (e.g. drill_camera_loss)")
    p.add_argument("--password", default="operator-dev")
    p.add_argument("--vision", action="store_true",
                   help="queues and the EV are RENDERED by a synthetic camera and MEASURED by the real vision pipeline "
                        "(needs opencv; run the backend with the physical capacities, see docs/VISION_SETUP.md)")
    a = p.parse_args()
    s = Settings()
    http = httpx.Client(base_url=a.url, timeout=3)
    topo = Topology.load(s.topology_path)
    devices = load_yaml(s.devices_path)["devices"]
    sim = VirtualTabletop(http, topo, devices, external_camera=a.vision,
                          rates={**{k: 0.08 for k in topo.approaches}, **{x: 0.5 for x in a.heavy.split(",") if x}})
    camera = None
    if a.vision:
        from simulator.vision_world import SyntheticCamera
        camera = SyntheticCamera(sim, http, devices)
        sim.ev_sink = camera.set_ev
    print("virtual tabletop running" + (" with synthetic camera + vision pipeline" if camera else "") + "; Ctrl+C to stop")
    sim.step(1000)                         # first control poll fixes the command cursor (no replay of old commands)
    if a.demo or a.scenario:
        start_scenario(a.url, a.password, a.scenario or "ambulance_demo")
    while True:
        sim.step(1000)
        if camera:
            camera.step()
        print(f"t={sim.now_ms/1000:6.1f}s  " + "  ".join(
            f"{i}:{f.phase[:1]}{f.axis}" for i, f in sim.fsms.items()) + "  q=" +
            ",".join(f"{k}{v}" for k, v in sim.queues.items() if v), flush=True)
        time.sleep(1.0 / a.speed)


if __name__ == "__main__":
    main()
