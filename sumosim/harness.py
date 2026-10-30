"""An isolated in-process backend + SumoWorld on a simulated clock (no sockets, no wall-clock waiting).
Same pattern as simulator.evaluate.build_world; used by the tests and by compare.py."""
import weakref
from pathlib import Path

import anyio.from_thread
from fastapi.testclient import TestClient

from app.config import ROOT, Settings, load_yaml
from app.core.topology import Topology
from app.main import create_app

from .build_network import build
from .sumo_bridge import SumoWorld

_NET = {}


def net_path() -> str:
    if "p" not in _NET:
        s = Settings()
        _NET["p"] = str(build(s.topology_path, Path(__file__).parent / "net"))
    return _NET["p"]


class Harness:
    def second(self):
        c = self.ctx
        c.incidents.tick(); c.ev.tick(); c.roadusers.tick(); c.controller.cycle()
        self.sim.step(1000)

    @property
    def now(self):
        return self.sim.now_ms / 1000

    def close(self):
        self.sim.close()


def build_world(seed: int, rates: dict, label: str = "h") -> Harness:
    holder = {}
    clock = lambda: holder["sim"].now_ms / 1000 if "sim" in holder else 0.0
    s = Settings(db_path=":memory:", operator_password="eval", controller_autostart=False,
                 llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="")
    app = create_app(s, clock)
    w = Harness()
    w.ctx, w.client = app.state.ctx, TestClient(app)
    portal_cm = anyio.from_thread.start_blocking_portal()     # one event loop per trial (see simulator.evaluate)
    w.client.portal = portal_cm.__enter__()
    weakref.finalize(w, portal_cm.__exit__, None, None, None)
    topo = Topology.load(s.topology_path)
    w.sim = SumoWorld(w.client, topo, load_yaml(s.devices_path)["devices"], net_path(), rates=rates, seed=seed, label=label)
    holder["sim"] = w.sim
    w.sim.step(1000)
    return w
