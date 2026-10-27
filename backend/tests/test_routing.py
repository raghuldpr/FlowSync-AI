import math

import pytest

from app.config import Settings
from app.core.routing import dijkstra, edge_costs, route_cost
from app.core.topology import Topology


@pytest.fixture(scope="module")
def topo():
    return Topology.load(Settings().topology_path)


def best(topo, queues=None, closed=(), src="ext_W", dst="ext_S"):
    return dijkstra(topo, edge_costs(topo, queues or {}, set(closed)), src, dst)


def test_free_flow_route_is_the_direct_ring_link(topo):
    path, cost = best(topo)
    assert path == ["W1", "L13", "O3"] and cost == pytest.approx(4 + 5 + 4)


def test_congestion_makes_the_alternative_win(topo):
    path, cost = best(topo, {"L13": 6})
    assert path == ["W1", "L12", "L23", "O3"]
    assert cost == pytest.approx(4 + 6 + 5 + 4)


def test_closure_removes_edge_and_unreachable_is_none(topo):
    assert best(topo, closed=["L13"])[0] == ["W1", "L12", "L23", "O3"]
    assert best(topo, closed=["L13", "L12"]) is None
    assert best(topo, closed=["O3"]) is None


def test_trivial_and_unknown_nodes(topo):
    assert best(topo, src="I1", dst="I1") == ([], 0.0)
    assert dijkstra(topo, edge_costs(topo, {}, set()), "I1", "nowhere") is None


def test_weights_must_be_finite_and_non_negative(topo):
    for bad in (-1.0, math.inf, math.nan):
        costs = edge_costs(topo, {}, set())
        costs["L13"] = bad
        with pytest.raises(ValueError):
            dijkstra(topo, costs, "ext_W", "ext_S")


def test_optimal_against_brute_force(topo):
    """Dijkstra cost equals the cheapest of all simple paths (graph is tiny)."""
    costs = edge_costs(topo, {"L13": 3, "L12": 1, "L23": 2, "L32": 4}, set())
    out = {}
    for e in topo.edges.values():
        out.setdefault(e.src, []).append(e)

    def all_paths(node, dst, seen):
        if node == dst:
            yield []
            return
        for e in out.get(node, []):
            if e.dst not in seen:
                for rest in all_paths(e.dst, dst, seen | {e.dst}):
                    yield [e.id] + rest
    for src, dst in [("ext_W", "ext_S"), ("ext_E", "ext_W"), ("I2", "I1")]:
        brute = min(route_cost(p, costs, topo) for p in all_paths(src, dst, {src}))
        assert dijkstra(topo, costs, src, dst)[1] == pytest.approx(brute)
