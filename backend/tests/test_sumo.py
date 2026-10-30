"""SUMO view tests. Skipped when SUMO / traci are not installed (the Python tabletop is the fallback)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sumosim = pytest.importorskip("sumosim")
if not sumosim.available():
    pytest.skip("SUMO not installed (pip install -r requirements-sumo.txt)", allow_module_level=True)

from app.config import Settings  # noqa: E402
from app.core.topology import Topology  # noqa: E402


def test_network_matches_topology(tmp_path):
    import sumolib
    from sumosim.build_network import build
    topo = Topology.load(Settings().topology_path)
    net = sumolib.net.readNet(str(build(Settings().topology_path, tmp_path)), withInternal=False)
    assert {e.getID() for e in net.getEdges()} == set(topo.edges)
    for a in topo.approaches.values():              # every approach edge ends at its intersection
        assert net.getEdge(a.id).getToNode().getID() == a.intersection


def test_triangle_runs_safely_and_ambulance_arrives():
    from sumosim.harness import build_world
    w = build_world(1, {"W1": 0.08, "E2": 0.08, "S3": 0.08}, label="test")
    try:
        w.sim.apply({"op": "pin", "approach": "L13", "count": 6})
        for _ in range(30):
            w.second()
        s = w.ctx.ev.declare("EV-1", "ext_W", "ext_S", "test", "t")
        w.sim.apply({"op": "drive_ev", "vehicle_id": "EV-1"})
        for _ in range(200):
            w.second()
            if s.status == "ended":
                break
        assert s.status == "ended", "ambulance did not arrive"
        assert "L13" not in s.route, "route should avoid the jammed link"
        assert not w.sim.unsafe_seen, "conflicting greens"
        assert w.sim.summary()["collisions"] == 0
    finally:
        w.close()
