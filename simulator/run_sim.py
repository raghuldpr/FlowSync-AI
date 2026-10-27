"""Run the virtual tabletop against a live backend:  python -m simulator.run_sim [--url http://127.0.0.1:8000]"""
import argparse
import time

import httpx

import simulator  # noqa: F401
from app.config import Settings, load_yaml
from app.core.topology import Topology
from simulator.tabletop import VirtualTabletop


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--speed", type=float, default=1.0, help="wall-clock speed factor for the loop sleep")
    p.add_argument("--heavy", default="", help="comma list of approaches with heavy arrivals, e.g. L12,W1")
    p.add_argument("--demo", action="store_true", help="run the timed demo scenario (needs the operator password)")
    p.add_argument("--password", default="operator-dev")
    a = p.parse_args()
    s = Settings()
    sim = VirtualTabletop(httpx.Client(base_url=a.url, timeout=3), Topology.load(s.topology_path),
                          load_yaml(s.devices_path)["devices"],
                          rates={**{k: 0.08 for k in Topology.load(s.topology_path).approaches},
                                 **{x: 0.5 for x in a.heavy.split(",") if x}})
    demo = None
    if a.demo:
        from simulator.demo import Demo
        demo = Demo(sim, a.url, a.password)
    print("virtual tabletop running; Ctrl+C to stop")
    while True:
        if demo:
            demo.step(sim.now_ms / 1000)
        sim.step(1000)
        print(f"t={sim.now_ms/1000:6.1f}s  " + "  ".join(
            f"{i}:{f.phase[:1]}{f.axis}" for i, f in sim.fsms.items()) + "  q=" +
            ",".join(f"{k}{v}" for k, v in sim.queues.items() if v), flush=True)
        time.sleep(1.0 / a.speed)


if __name__ == "__main__":
    main()
