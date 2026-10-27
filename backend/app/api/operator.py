import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import auth
from ..audit import audit
from ..db import now_iso
from ..deps import client_id, get_ctx, require_operator

router = APIRouter(tags=["operator"])


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class EmergencyIn(BaseModel):
    approach: str
    vehicle_id: str = Field(default="EV-1", max_length=32)


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, ctx=Depends(get_ctx)):
    ip, now = client_id(request), time.monotonic()
    fails = [t for t in ctx.login_failures.get(ip, []) if now - t < 60]
    if len(fails) >= 5:
        audit(ctx.db, ip, "public", "POST /api/auth/login", result="denied_rate_limited")
        raise HTTPException(429, "too many attempts")
    token = auth.login(ctx.db, body.username, body.password, ctx.settings.session_ttl_s)
    if token is None:
        ctx.login_failures[ip] = fails + [now]
        audit(ctx.db, body.username[:64], "public", "login", result="denied_bad_credentials")
        raise HTTPException(401, "invalid credentials")
    audit(ctx.db, body.username, "operator", "login")
    return {"token": token, "role": "operator"}


@router.get("/api/operator/traffic")
def traffic(_=Depends(require_operator), ctx=Depends(get_ctx)):
    now = ctx.store.clock()
    stale = ctx.topology.control.stale_after_s
    return {a: {"count": r["count"], "age_s": round(now - r["recv_t"], 2), "stale": now - r["recv_t"] > stale,
                "source": r["source"]} for a, r in ctx.store.traffic.items()}


@router.get("/api/operator/signals")
def signals(_=Depends(require_operator), ctx=Depends(get_ctx)):
    """Shows what the DEVICE reports, next to what was commanded; flags stale and mismatching state."""
    c, tm, out = ctx.topology.control, ctx.topology.timing, {}
    now = ctx.store.clock()
    for inter in ctx.topology.intersections:
        device = ctx.intersection_device.get(inter)
        cmd = next((x for x in ctx.store.get_commands(device) if x["intersection"] == inter), None) if device else None
        raw = ctx.store.actual.get(inter)
        rep = raw["state"] if raw else None
        stale = raw is None or now - raw["recv_t"] > c.stale_after_s
        mismatch = False
        if cmd and rep and not stale:
            settle_s = (tm.min_green_ms + tm.yellow_ms + tm.all_red_ms) / 1000 + 3
            heading = rep["axis"] if rep["phase"] == "GREEN" else rep["next_axis"]
            mismatch = heading != cmd["target"] and now - cmd["changed_t"] > settle_s
        out[inter] = {
            "commanded": {k: cmd[k] for k in ("seq", "target", "reason")} if cmd else None,
            "reported": rep, "reported_stale": stale, "mismatch": mismatch,
        }
    return out


@router.get("/api/operator/decisions")
def decisions(limit: int = Query(50, ge=1, le=500), _=Depends(require_operator), ctx=Depends(get_ctx)):
    rows = ctx.db.query("SELECT * FROM signal_decisions ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["inputs"] = json.loads(r["inputs"]) if r["inputs"] else None
    return rows


@router.get("/api/operator/events")
def events(limit: int = Query(50, ge=1, le=500), _=Depends(require_operator), ctx=Depends(get_ctx)):
    return ctx.db.query("SELECT * FROM system_events ORDER BY id DESC LIMIT ?", (limit,))


@router.get("/api/operator/audit")
def audit_log(limit: int = Query(100, ge=1, le=1000), _=Depends(require_operator), ctx=Depends(get_ctx)):
    return ctx.db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


@router.get("/api/operator/devices")
def devices(_=Depends(require_operator), ctx=Depends(get_ctx)):
    now = ctx.store.clock()
    return [{"device_id": d, "role": s["role"],
             "age_s": round(now - ctx.device_seen[d], 1) if d in ctx.device_seen else None}
            for d, s in ctx.device_specs.items()]


@router.post("/api/operator/emergencies")
def create_emergency(body: EmergencyIn, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    if body.approach not in ctx.topology.approaches:
        raise HTTPException(422, "unknown approach")
    eid = ctx.db.execute(
        "INSERT INTO emergency_requests(source,vehicle_id,approach,status,created_by,created_at) VALUES(?,?,?,?,?,?)",
        ("operator", body.vehicle_id, body.approach, "active", sess["username"], now_iso()))
    audit(ctx.db, sess["username"], "operator", "emergency_create", str(eid), body.model_dump())
    return {"id": eid}


@router.get("/api/operator/emergencies")
def list_emergencies(_=Depends(require_operator), ctx=Depends(get_ctx)):
    return ctx.db.query("SELECT * FROM emergency_requests ORDER BY id DESC LIMIT 50")


@router.post("/api/operator/emergencies/{eid}/clear")
def clear_emergency(eid: int, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    row = ctx.db.one("SELECT status FROM emergency_requests WHERE id=?", (eid,))
    if row is None:
        raise HTTPException(404, "not found")
    ctx.db.execute("UPDATE emergency_requests SET status='cleared', cleared_at=?, cleared_by=? WHERE id=?",
                   (now_iso(), sess["username"], eid))
    audit(ctx.db, sess["username"], "operator", "emergency_clear", str(eid))
    return {"ok": True}
