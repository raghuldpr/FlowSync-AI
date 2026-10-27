from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ..audit import audit
from ..core.hazards import KINDS, SEVERITIES
from ..deps import require_viewer, client_id, get_ctx, require_operator

router = APIRouter(tags=["hazards"])


class HazardIn(BaseModel):
    kind: Literal["pothole", "debris", "wet_surface"]
    edge: str = Field(max_length=16)
    severity: Literal["low", "medium", "high"] = "medium"


class ReportIn(BaseModel):
    model_config = {"extra": "forbid"}                      # the location comes from the session, never from the client
    token: str = Field(max_length=64)
    kind: Literal["pothole", "debris", "wet_surface"]


@router.post("/api/public/hazards")
def report(body: ReportIn, request: Request, ctx=Depends(get_ctx)):
    """A road user flags a hazard at THEIR OWN current position. It is only a report: it changes nothing until an operator verifies it."""
    u = ctx.roadusers.get(body.token)
    if u is None:
        raise HTTPException(404, "unknown or expired session")
    if not (ctx.rate.allow(f"hz-tok:{body.token}", 5, 60) and ctx.rate.allow(f"hz-ip:{client_id(request)}", 20, 60)):
        raise HTTPException(429, "too many reports; wait a minute")
    try:
        ctx.hazards.create(body.kind, u.edge, "low", "public", status="reported", progress=u.progress)
    except OverflowError:
        raise HTTPException(503, "the review queue is full; please try again later")
    return {"ok": True, "message": "Thank you. An operator will review this report."}


@router.get("/api/operator/hazards")
def list_hazards(status: Literal["reported", "open", "resolved", "rejected", "active"] = None, limit: int = Query(50, ge=1, le=200),
                 _=Depends(require_viewer), ctx=Depends(get_ctx)):
    return ctx.hazards.list(status, limit)


@router.post("/api/operator/hazards")
def create_hazard(body: HazardIn, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    if body.edge not in ctx.topology.edges:
        raise HTTPException(422, "unknown road")
    hid = ctx.hazards.create(body.kind, body.edge, body.severity, "operator", actor=sess["username"])
    audit(ctx.db, sess["username"], sess["role"], "hazard_create", str(hid), body.model_dump())
    return {"id": hid}


def _act(name):
    def handler(hid: int, sess=Depends(require_operator), ctx=Depends(get_ctx)):
        ok = getattr(ctx.hazards, name)(hid, sess["username"])
        audit(ctx.db, sess["username"], sess["role"], f"hazard_{name}", str(hid), result="ok" if ok else "not_applicable")
        if not ok:
            raise HTTPException(409, f"cannot {name} this hazard in its current state")
        return {"ok": True}
    handler.__name__ = f"hazard_{name}"
    return handler


for _n in ("verify", "reject", "resolve"):
    router.add_api_route(f"/api/operator/hazards/{{hid}}/{_n}", _act(_n), methods=["POST"])


@router.get("/api/operator/images/{image_id}")
def image(image_id: str, _=Depends(require_viewer), ctx=Depends(get_ctx)):
    if len(image_id) != 32 or not all(c in "0123456789abcdef" for c in image_id):
        raise HTTPException(404, "no such image")
    row = ctx.db.one("SELECT path FROM images WHERE id=?", (image_id,))
    if row is None:
        raise HTTPException(404, "no such image")
    try:
        data = open(row["path"], "rb").read()
    except OSError:
        raise HTTPException(404, "image no longer available")
    return Response(data, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"})
