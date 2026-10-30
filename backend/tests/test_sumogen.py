"""SUMO network generation: the generated files must agree with the FlowSync topology they came from."""
import json
from xml.etree import ElementTree as ET

import pytest

from app import sumogen
from app.config import ROOT
from app.core.topology import Topology

FILES = {"triangle": ROOT / "config" / "topology.yaml", "grid": ROOT / "config" / "topology.grid.yaml"}


@pytest.fixture(scope="module", params=sorted(FILES))
def gen(request, tmp_path_factory):
    out = tmp_path_factory.mktemp(request.param)
    topo = Topology.load(str(FILES[request.param]))
    sumogen.write(topo, out, request.param)
    return topo, out, json.loads((out / "flowsync_map.json").read_text())


def test_writes_every_input_file_as_wellformed_xml(gen):
    _, out, _ = gen
    for name in ("flowsync.nod.xml", "flowsync.edg.xml", "routes.rou.xml", "flowsync.sumocfg"):
        assert ET.parse(out / name).getroot() is not None, name


def test_nodes_and_edges_match_the_topology(gen):
    topo, out, _ = gen
    nodes = {n.get("id"): n for n in ET.parse(out / "flowsync.nod.xml").getroot()}
    assert set(nodes) == set(topo.nodes)
    for i in topo.intersections:
        assert nodes[i].get("type") == "traffic_light" and nodes[i].get("tl") == i
    assert all(nodes[n].get("type") == "dead_end" for n in set(topo.nodes) - set(topo.intersections))
    assert len({(n.get("x"), n.get("y")) for n in nodes.values()}) == len(nodes), "two nodes share a position"
    edges = {e.get("id"): e for e in ET.parse(out / "flowsync.edg.xml").getroot()}
    assert set(edges) == set(topo.edges)
    for eid, e in topo.edges.items():
        assert (edges[eid].get("from"), edges[eid].get("to")) == (e.src, e.dst)


def test_every_route_is_connected_and_never_u_turns(gen):
    topo, out, _ = gen
    routes = [r for r in ET.parse(out / "routes.rou.xml").getroot().iter("route") if r.get("edges")]
    assert routes
    for r in routes:
        ids = r.get("edges").split()
        for a, b in zip(ids, ids[1:]):
            assert topo.edges[a].dst == topo.edges[b].src, f"{r.get('id')}: {a} does not lead into {b}"
            assert topo.edges[b].dst != topo.edges[a].src, f"{r.get('id')}: U-turn {a} -> {b}"


def test_flows_only_reference_defined_routes(gen):
    _, out, _ = gen
    root = ET.parse(out / "routes.rou.xml").getroot()
    defined = {r.get("id") for r in root.iter("route") if r.get("id")} | {d.get("id") for d in root.iter("routeDistribution")}
    for f in root.iter("flow"):
        assert f.get("route") in defined
    for d in root.iter("routeDistribution"):
        assert all(r.get("refId") in defined for r in d)


def test_axis_map_covers_every_approach_exactly_once(gen):
    topo, _, m = gen
    seen = [a for axes in m["intersections"].values() for lst in axes.values() for a in lst]
    assert sorted(seen) == sorted(topo.approaches)
    for i, axes in m["intersections"].items():
        for axis, lst in axes.items():
            assert all(topo.approaches[a].intersection == i and topo.approaches[a].movement == axis for a in lst)


def test_sumocfg_points_at_the_files_it_needs(gen):
    _, out, m = gen
    cfg = ET.parse(out / "flowsync.sumocfg").getroot()
    assert cfg.find("input/net-file").get("value") == m["net_file"] == sumogen.NET
    assert (out / cfg.find("input/route-files").get("value")).exists()
    assert cfg.find("processing/time-to-teleport").get("value") == "-1"


def test_tabletop_ambulance_takes_the_documented_paths():
    """docs/IMPLEMENTATION_PLAN.md: EV demo trip W1 -> S3 area, path 1 via L13, path 2 via L12 and L23."""
    topo = Topology.load(str(FILES["triangle"]))
    ev = sumogen.build(topo, "t")["map"]["ev_routes"]
    assert ev["ev_default"] == ["W1", "L13", "O3"]
    assert ev["ev_alt"] == ["W1", "L12", "L23", "O3"]
    veh = [v for v in sumogen.build(topo, "t")["ev_baseline.rou.xml"].iter("vehicle")][0]
    assert veh.get("type") == "ev" and veh.get("id") == "EV-1"


def test_emergency_vehicle_uses_the_emergency_vclass():
    root = sumogen.build(Topology.load(str(FILES["triangle"])), "t")["routes.rou.xml"]
    assert {v.get("id"): v.get("vClass") for v in root.iter("vType")}["ev"] == "emergency"


def test_missing_layout_is_a_clear_error():
    topo = Topology.load(str(FILES["triangle"]))
    del topo.layout["I2"]
    with pytest.raises(ValueError, match="I2"):
        sumogen.build(topo, "t")


def test_build_without_netconvert_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.delenv("SUMO_HOME", raising=False)
    monkeypatch.setattr(sumogen.shutil, "which", lambda _: None)
    assert sumogen.run_netconvert(tmp_path) == 2
