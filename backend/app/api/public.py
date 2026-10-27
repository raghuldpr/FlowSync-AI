from fastapi import APIRouter, Depends

from ..deps import get_ctx

router = APIRouter(tags=["public"])


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/public/signals")
def public_signals(ctx=Depends(get_ctx)):
    """Lamp colours only, and only while the device report is fresh. No timing internals."""
    stale = ctx.topology.control.stale_after_s
    out = {}
    for inter in ctx.topology.intersections:
        st = ctx.store.get_actual(inter, stale)
        out[inter] = st["lamps"] if st else "unknown"
    return out


@router.get("/api/public/congestion")
def public_congestion(ctx=Depends(get_ctx)):
    """Coarse bands only; never raw counts, sources or history."""
    cap = ctx.topology.control.link_capacity
    q = ctx.store.fresh_traffic(ctx.topology.control.stale_after_s)
    out = {}
    for a in ctx.topology.approaches:
        c = q.get(a)
        out[a] = "unknown" if c is None else ("low" if c / cap < 0.34 else "medium" if c / cap < 0.67 else "high")
    return out
