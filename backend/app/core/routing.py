"""Standard Dijkstra on a directed graph with a per-search edge-cost snapshot.

T_e = L_e / V_e + D_e   (seconds; D_e = delay_per_vehicle * queued vehicles on the edge's ROI)
Closed edges are removed from the search; weights must be finite and non-negative.
"""
import heapq
import math
from typing import Optional


def edge_costs(topology, queues: dict, closed: set) -> dict:
    """Cost snapshot for open edges. Unknown/stale queue = 0 extra delay (free-flow), never negative."""
    d = topology.routing.delay_per_vehicle_s
    out = {}
    for eid, e in topology.edges.items():
        if eid in closed:
            continue
        q = queues.get(eid)
        out[eid] = e.length / e.speed + d * (q or 0)
    return out


def dijkstra(topology, costs: dict, src: str, dst: str) -> Optional[tuple]:
    """Returns (edge_id_path, total_cost) or None when unreachable. Only edges present in `costs` are usable."""
    for eid, c in costs.items():
        if not math.isfinite(c) or c < 0:
            raise ValueError(f"edge {eid}: cost must be finite and non-negative, got {c}")
    if src not in topology.nodes or dst not in topology.nodes:
        return None
    if src == dst:
        return [], 0.0
    adj = {}
    for eid, c in costs.items():
        adj.setdefault(topology.edges[eid].src, []).append((topology.edges[eid].dst, eid, c))
    dist, prev, heap = {src: 0.0}, {}, [(0.0, src)]
    while heap:
        d, n = heapq.heappop(heap)
        if d > dist.get(n, math.inf):
            continue
        if n == dst:
            break
        for m, eid, c in adj.get(n, ()):
            nd = d + c
            if nd < dist.get(m, math.inf):
                dist[m], prev[m] = nd, (n, eid)
                heapq.heappush(heap, (nd, m))
    if dst not in dist:
        return None
    path, n = [], dst
    while n != src:
        n, eid = prev[n]
        path.append(eid)
    return path[::-1], dist[dst]


def route_cost(path: list, costs: dict, topology) -> float:
    """Cost of a path under a snapshot; a closed edge makes it infinite."""
    return sum(costs[e] if e in costs else math.inf for e in path)
