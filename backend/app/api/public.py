from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_ctx

router = APIRouter(tags=["public"])


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/public/topology")
def public_topology(ctx=Depends(get_ctx)):
    """Static map geometry for drawing; no live data."""
    t = ctx.topology
    return {
        "nodes": {n: {"x": t.layout[n][0], "y": t.layout[n][1], "label": t.node_labels.get(n, n),
                      "kind": "intersection" if n in t.intersections else "external"} for n in t.nodes if n in t.layout},
        "edges": [{"id": e.id, "src": e.src, "dst": e.dst, "approach": e.id in t.approaches,
                   "movement": t.approaches[e.id].movement if e.id in t.approaches else None}
                  for e in t.edges.values()],
        "lane_offset": t.vision.get("lane_offset", 14),
        "capacity": t.control.link_capacity,
        "capacities": {a.id: a.capacity for a in t.approaches.values()},
    }


@router.get("/api/public/signals")
def public_signals(ctx=Depends(get_ctx)):
    """Lamp colours only, and only while the device report is fresh. No timing internals."""
    stale = ctx.topology.control.stale_after_s
    out = {}
    for inter in ctx.topology.intersections:
        st = ctx.store.get_actual(inter, stale)
        out[inter] = st["lamps"] if st else "unknown"
    return out


@router.get("/api/public/warnings")
def public_warnings(approach: str = Query(max_length=16), ctx=Depends(get_ctx)):
    """Driver view for one approach: fixed-template text and a coarse ETA band, or null."""
    if approach not in ctx.topology.approaches:
        raise HTTPException(404, "unknown approach")
    return {"approach": approach, "warning": ctx.ev.public_warning(approach)}


@router.get("/api/public/congestion")
def public_congestion(ctx=Depends(get_ctx)):
    """Coarse bands only; never raw counts, sources or history."""
    q = ctx.store.fresh_traffic(ctx.topology.control.stale_after_s)
    out = {}
    for a, ap in ctx.topology.approaches.items():
        cap, c = ap.capacity, q.get(a)
        out[a] = "unknown" if c is None else ("low" if c / cap < 0.34 else "medium" if c / cap < 0.67 else "high")
    return out
