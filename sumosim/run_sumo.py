"""Run the SUMO view against a live backend:  python -m sumosim.run_sumo [--gui] [--scenario ambulance_demo]

Start the backend first (see README). The SUMO world is SIMULATED; the backend cannot tell it apart from the tabletop.
"""
import argparse
import time
from pathlib import Path

import httpx

from app.config import Settings, load_yaml
from app.core.topology import Topology

from . import available
from .build_network import build
from .sumo_bridge import ENTRIES, SumoWorld

PRESETS = {"calm": {a: 0.05 for a in ENTRIES}, "normal": {a: 0.10 for a in ENTRIES},
           "rush": {"W1": 0.25, "E2": 0.10, "S3": 0.10}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--gui", action="store_true", help="open SUMO-GUI (otherwise headless)")
    p.add_argument("--speed", type=float, default=1.0, help="wall-clock speed factor (1 = real time)")
    p.add_argument("--demand", choices=PRESETS, default="normal")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--demo", action="store_true", help="start the 'ambulance_demo' scenario (needs the operator password)")
    p.add_argument("--scenario", help="start this scenario id instead (e.g. drill_camera_loss)")
    p.add_argument("--password", default="operator-dev")
    p.add_argument("--duration", type=float, default=0, help="stop after this many simulated seconds (0 = until Ctrl+C / GUI closed)")
    a = p.parse_args()
    if not available():
        raise SystemExit("SUMO not available: pip install eclipse-sumo traci sumolib")
    s = Settings()
    topo = Topology.load(s.topology_path)
    net = build(s.topology_path, Path(__file__).parent / "net")
    http = httpx.Client(base_url=a.url, timeout=3)
    world = SumoWorld(http, topo, load_yaml(s.devices_path)["devices"], str(net), rates=PRESETS[a.demand], seed=a.seed, gui=a.gui)
    print("SUMO view running (SIMULATED); Ctrl+C to stop")
    try:
        world.step(1000)
        if a.demo or a.scenario:
            from simulator.run_sim import start_scenario
            start_scenario(a.url, a.password, a.scenario or "ambulance_demo")
        t0 = time.time()
        while a.duration <= 0 or world.now_ms / 1000 < a.duration:
            world.step(1000)
            print(f"t={world.now_ms/1000:6.1f}s  " + "  ".join(f"{i}:{f.phase[:1]}{f.axis}" for i, f in world.fsms.items())
                  + f"  in zones={world.queue_total()}  EV={world.ev_where()}", flush=True)
            time.sleep(max(0.0, t0 + world.now_ms / 1000 / a.speed - time.time()))
    except KeyboardInterrupt:
        pass
    except Exception as e:                      # SUMO-GUI closed by the user
        print("stopped:", e)
    finally:
        print(world.summary())
        world.close()


if __name__ == "__main__":
    main()
