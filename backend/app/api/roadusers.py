import random
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..audit import audit
from ..deps import require_viewer, client_id, get_ctx, require_operator

router = APIRouter(tags=["road-users"])


class JoinIn(BaseModel):
    """Guest join. No name, no form: a normal vehicle may send nothing at all (random trip); an ambulance picks its trip."""
    origin: Optional[str] = Field(default=None, max_length=16)
    destination: Optional[str] = Field(default=None, max_length=16)
    role: Literal["vehicle", "ambulance"] = "vehicle"


def _user(ctx, token: str):
    u = ctx.roadusers.get(token) if len(token) < 64 else None
    if u is None:
        raise HTTPException(404, "unknown or expired session")     # same answer for 'never existed' and 'expired'
    return u


@router.post("/api/public/road-users")
def join(body: JoinIn, request: Request, ctx=Depends(get_ctx)):
    """Anonymous: pick where you enter and where you are going; you get a pseudonym, a token and your own alert feed."""
    if not ctx.rate.allow(f"join:{client_id(request)}", ctx.settings.join_per_min, 60):
        raise HTTPException(429, "too many sessions from this address; wait a minute")
    topo = ctx.topology
    ext = sorted(n for n in topo.nodes if n not in topo.intersections)
    origin, destination = body.origin, body.destination
    if body.role == "ambulance":
        origin = origin or topo.ev.default_origin
        destination = destination or topo.ev.default_destination
        if not ctx.rate.allow(f"join-amb:{client_id(request)}", ctx.settings.join_ambulance_per_min, 60):
            raise HTTPException(429, "too many ambulance trips from this address; wait a minute")
    else:
        origin = origin or random.choice(ext)                                   # QR gate link may pin the entrance (?gate=ext_W)
        destination = destination or random.choice([n for n in ext if n != origin])
    try:
        u = ctx.roadusers.join(origin, destination, role=body.role)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except OverflowError as e:
        raise HTTPException(503, str(e) if "ambulance" in str(e) else "the demo is full right now")
    if body.role == "ambulance":
        audit(ctx.db, u.id, "guest", "guest_ambulance_start", u.ev_vehicle_id, {"origin": origin, "destination": destination})
    return {"token": u.token, **ctx.roadusers.view(u)}


@router.get("/api/public/road-users/{token}")
def my_view(token: str, ctx=Depends(get_ctx)):
    return ctx.roadusers.view(_user(ctx, token))


@router.get("/api/public/road-users/{token}/poll")
def poll(token: str, after: int = Query(-1), timeout: float = Query(10.0, ge=0.0, le=25.0), ctx=Depends(get_ctx)):
    """Long-poll push: returns the moment this user's view changes (or after `timeout` with the current view)."""
    _user(ctx, token)
    v = ctx.roadusers.wait_change(token, after, timeout)
    if v is None:
        raise HTTPException(404, "unknown or expired session")
    return v


@router.post("/api/public/road-users/{token}/route/accept")
def accept(token: str, ctx=Depends(get_ctx)):
    _user(ctx, token)
    if not ctx.roadusers.accept_suggestion(token):
        raise HTTPException(409, "no suggested route to accept")
    return ctx.roadusers.view(_user(ctx, token))


@router.post("/api/public/road-users/{token}/alert")
def raise_alert(token: str, request: Request, ctx=Depends(get_ctx)):
    """A guest AMBULANCE raises a simulated V2V emergency alert. Role is checked here, never on the client; rate-limited."""
    u = _user(ctx, token)
    if u.role != "ambulance":
        audit(ctx.db, u.id, "guest", "v2v_alert", token[:6], result="denied_role")
        raise HTTPException(403, "only an ambulance can raise an emergency alert")
    if not ctx.rate.allow(f"v2v:{token}", 6, 60):
        raise HTTPException(429, "too many alerts; wait a minute")
    try:
        res = ctx.roadusers.raise_alert(token)
    except RuntimeError as e:
        raise HTTPException(429, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(ctx.db, u.id, "guest", "v2v_alert", u.ev_vehicle_id, {"reached": res["reached"], "simulated": True})
    return res


@router.delete("/api/public/road-users/{token}")
def leave(token: str, ctx=Depends(get_ctx)):
    _user(ctx, token)
    ctx.roadusers.leave(token)
    return {"ok": True}


@router.get("/api/operator/road-users")
def operator_list(_=Depends(require_viewer), ctx=Depends(get_ctx)):
    return ctx.roadusers.snapshot()


@router.get("/api/operator/road-users/feed")
def operator_feed(_=Depends(require_viewer), ctx=Depends(get_ctx)):
    """Big-screen feed: all cars (id, colour, plate, role, edge, progress, warned), guest ambulances, recent simulated V2V alerts."""
    return ctx.roadusers.feed()
