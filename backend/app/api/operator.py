import json
import time
from typing import Literal

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


class EVIn(BaseModel):
    vehicle_id: str = Field(default="EV-1", max_length=32)
    origin: str | None = None
    destination: str | None = None


class ClosedIn(BaseModel):
    closed: bool


class IncidentIn(BaseModel):
    type: Literal["congestion_spike", "stalled_vehicle"]
    approach: str


@router.get("/api/operator/incidents")
def list_incidents(status: Literal["open", "acknowledged", "resolved", "active"] = None,
                   limit: int = Query(50, ge=1, le=200), _=Depends(require_operator), ctx=Depends(get_ctx)):
    return ctx.incidents.list(status, limit)


@router.post("/api/operator/incidents")
def create_incident(body: IncidentIn, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    """Operator-declared incident (e.g. reported by marshals, or injected for a demo)."""
    if body.approach not in ctx.topology.approaches:
        raise HTTPException(422, "unknown approach")
    iid = ctx.incidents.create(body.type, body.approach, "operator", {"declared_by": sess["username"]})
    audit(ctx.db, sess["username"], "operator", "incident_create", str(iid), body.model_dump(),
          "ok" if iid else "duplicate")
    if iid is None:
        raise HTTPException(409, "an open incident of this type already exists on that approach")
    return {"id": iid}


@router.post("/api/operator/incidents/{iid}/ack")
def ack_incident(iid: int, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    ok = ctx.incidents.acknowledge(iid, sess["username"])
    audit(ctx.db, sess["username"], "operator", "incident_ack", str(iid), result="ok" if ok else "not_open")
    if not ok:
        raise HTTPException(409, "incident is not open")
    return {"ok": True}


@router.post("/api/operator/incidents/{iid}/resolve")
def resolve_incident(iid: int, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    ok = ctx.incidents.resolve(iid, sess["username"], "operator_resolved")
    audit(ctx.db, sess["username"], "operator", "incident_resolve", str(iid), result="ok" if ok else "not_active")
    if not ok:
        raise HTTPException(404, "no such active incident")
    return {"ok": True}


@router.get("/api/operator/overview")
def overview(_=Depends(require_operator), ctx=Depends(get_ctx)):
    """One call for the operator dashboard."""
    return {
        "traffic": traffic(ctx=ctx),
        "signals": signals(ctx=ctx),
        "decisions": ctx.store.last_full,
        "ev": ctx.ev.snapshot(),
        "incidents": ctx.incidents.list("active"),
        "alerts_unacknowledged": ctx.incidents.unacknowledged(),
        "closed_edges": sorted(ctx.store.all_closed()),
        "operator_closed_edges": sorted(ctx.store.closed_edges),
        "proposals": ctx.proposals.recent(8),
        "llm": ctx.llm.status(),
        "events": ctx.db.query("SELECT * FROM system_events ORDER BY id DESC LIMIT 25"),
        "warning_events": ctx.db.query(
            "SELECT e.ts,e.from_state,e.to_state,e.reason,w.approach FROM ev_warning_events e "
            "JOIN ev_warnings w ON w.id=e.warning_id ORDER BY e.id DESC LIMIT 15"),
        "devices": devices(ctx=ctx),
    }


@router.post("/api/operator/ev")
def declare_ev(body: EVIn, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    o = body.origin or ctx.topology.ev.default_origin
    d = body.destination or ctx.topology.ev.default_destination
    if o not in ctx.topology.nodes or d not in ctx.topology.nodes:
        raise HTTPException(422, "unknown node")
    try:
        s = ctx.ev.declare(body.vehicle_id, o, d, "operator", sess["username"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(ctx.db, sess["username"], "operator", "ev_declare", str(s.id), {"vehicle_id": body.vehicle_id, "origin": o, "destination": d})
    return {"session": s.id, "route": s.route}


@router.get("/api/operator/ev")
def list_ev(_=Depends(require_operator), ctx=Depends(get_ctx)):
    return ctx.ev.snapshot()


@router.post("/api/operator/ev/{sid}/clear")
def clear_ev(sid: int, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    ok = ctx.ev.end(sid, "operator_cleared")
    audit(ctx.db, sess["username"], "operator", "ev_clear", str(sid), result="ok" if ok else "not_active")
    if not ok:
        raise HTTPException(404, "no active session")
    return {"ok": True}


@router.get("/api/operator/ev/warnings")
def ev_warning_history(limit: int = Query(100, ge=1, le=500), _=Depends(require_operator), ctx=Depends(get_ctx)):
    return ctx.db.query("SELECT e.*, w.approach, w.session_id FROM ev_warning_events e "
                        "JOIN ev_warnings w ON w.id=e.warning_id ORDER BY e.id DESC LIMIT ?", (limit,))


@router.post("/api/operator/edges/{edge}/closed")
def set_edge_closed(edge: str, body: ClosedIn, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    if edge not in ctx.topology.edges:
        raise HTTPException(404, "unknown edge")
    (ctx.store.closed_edges.add if body.closed else ctx.store.closed_edges.discard)(edge)
    audit(ctx.db, sess["username"], "operator", "edge_closed" if body.closed else "edge_opened", edge)
    return {"closed": sorted(ctx.store.closed_edges)}


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
