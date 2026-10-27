from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..deps import require_viewer, client_id, get_ctx, require_operator

router = APIRouter(tags=["road-users"])


class JoinIn(BaseModel):
    origin: str = Field(max_length=16)
    destination: str = Field(max_length=16)


def _user(ctx, token: str):
    u = ctx.roadusers.get(token) if len(token) < 64 else None
    if u is None:
        raise HTTPException(404, "unknown or expired session")     # same answer for 'never existed' and 'expired'
    return u


@router.post("/api/public/road-users")
def join(body: JoinIn, request: Request, ctx=Depends(get_ctx)):
    """Anonymous: pick where you enter and where you are going; you get a pseudonym, a token and your own alert feed."""
    if not ctx.rate.allow(f"join:{client_id(request)}", 10, 60):
        raise HTTPException(429, "too many sessions from this address; wait a minute")
    try:
        u = ctx.roadusers.join(body.origin, body.destination)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except OverflowError:
        raise HTTPException(503, "the demo is full right now")
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


@router.delete("/api/public/road-users/{token}")
def leave(token: str, ctx=Depends(get_ctx)):
    _user(ctx, token)
    ctx.roadusers.leave(token)
    return {"ok": True}


@router.get("/api/operator/road-users")
def operator_list(_=Depends(require_viewer), ctx=Depends(get_ctx)):
    return ctx.roadusers.snapshot()
