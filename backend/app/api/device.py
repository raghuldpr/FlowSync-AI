from typing import Dict, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from ..audit import audit, event
from ..db import now_iso
from ..core.validator import lamps_safe
from ..deps import DeviceCall, device_call, get_ctx, need_role

router = APIRouter(prefix="/api/device", tags=["device"])


class Reading(BaseModel):
    approach: str
    count: int = Field(ge=0, le=64)
    stationary_s: float = Field(default=0.0, ge=0.0, le=3600.0)   # longest time an object in the ROI has not moved


class TrafficIn(BaseModel):
    source: Literal["overhead_camera", "recorded_video", "simulated", "ultrasonic"]
    readings: list[Reading] = Field(max_length=256)        # one per approach; the largest generated grid (5 x 6) has 120


class InterState(BaseModel):
    phase: Literal["GREEN", "YELLOW", "ALL_RED"]
    axis: Literal["A", "B"]
    next_axis: Literal["A", "B"]
    elapsed_ms: int = Field(ge=0)
    lamps: Dict[Literal["A", "B"], Literal["R", "Y", "G"]]
    backend_ok: bool = True


class StateIn(BaseModel):
    intersections: Dict[str, InterState]
    origin: Literal["hardware", "simulated"] = "hardware"      # lets the dashboard state truthfully what is built vs simulated


class AckIn(BaseModel):
    seq: int
    intersection: str
    accepted: bool
    reason: str = Field(max_length=64)


def parse(model, data):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False, include_input=False))


@router.post("/traffic")
def post_traffic(call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    need_role(call, "camera", "intersection_controller")
    body = parse(TrafficIn, call.data)
    for r in body.readings:
        if r.approach not in ctx.topology.approaches:
            raise HTTPException(422, f"unknown approach {r.approach}")
    ctx.prov["traffic"] = (body.source, ctx.store.clock())
    for r in body.readings:
        ctx.store.set_traffic(r.approach, r.count, body.source, r.stationary_s)
        ctx.db.execute("INSERT INTO traffic_snapshots(ts,source,device_id,approach,count) VALUES(?,?,?,?,?)",
                       (now_iso(), body.source, call.device_id, r.approach, r.count))
    return {"ok": True}


class EVPositionIn(BaseModel):
    vehicle_id: str = Field(max_length=32)
    edge: str
    progress: float = Field(ge=0.0, le=1.0)
    source: Literal["camera", "simulated"] = "camera"


class EVDeclareIn(BaseModel):
    vehicle_id: str = Field(default="EV-1", max_length=32)
    origin: str | None = None
    destination: str | None = None


@router.post("/ev-position")
def post_ev_position(call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    need_role(call, "camera", "ev_car")
    body = parse(EVPositionIn, call.data)
    if body.edge not in ctx.topology.edges:
        raise HTTPException(422, "unknown edge")
    ctx.prov["ev_position"] = (body.source, ctx.store.clock())
    with ctx.metrics.timer("ev_position_to_alert_ms"):         # position in -> every affected road user's alert updated
        tracked = ctx.ev.position(body.vehicle_id, body.edge, body.progress)
        ctx.roadusers.refresh()
    return {"tracked": tracked}


@router.post("/ev-declare")
def post_ev_declare(call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    """Emergency button on the connected car: declares emergency mode (still validated by the backend)."""
    need_role(call, "ev_car")
    body = parse(EVDeclareIn, call.data)
    o = body.origin or ctx.topology.ev.default_origin
    d = body.destination or ctx.topology.ev.default_destination
    try:
        s = ctx.ev.declare(body.vehicle_id, o, d, "device_button", call.device_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(ctx.db, call.device_id, "device", "ev_declare", str(s.id), body.model_dump())
    return {"session": s.id, "route": s.route}


@router.post("/state")
def post_state(call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    need_role(call, "intersection_controller")
    body = parse(StateIn, call.data)
    allowed = set(call.spec.get("intersections", []))
    for inter, st in body.intersections.items():
        if inter not in allowed:
            raise HTTPException(403, f"device not responsible for {inter}")
    ctx.prov["controller"] = (body.origin, ctx.store.clock())
    for inter, st in body.intersections.items():
        if not lamps_safe(st.lamps):
            event(ctx.db, "unsafe_lamps_reported", call.device_id, "critical",
                  {"intersection": inter, "lamps": st.lamps})
        ctx.store.set_actual(inter, st.model_dump(), call.device_id)
    return {"ok": True}


@router.get("/commands")
def get_commands(call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    need_role(call, "intersection_controller")
    cmds = [{k: v for k, v in c.items() if k != "changed_t"} for c in ctx.store.get_commands(call.device_id)]
    return {"commands": cmds}


class ImageIn(BaseModel):
    edge: str | None = Field(default=None, max_length=16)        # optional: the camera rides on the tracked car, so the backend knows where it is
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    image_b64: str = Field(max_length=600_000)             # signed with the rest of the body; base64 of a JPEG <= 400 KB


@router.post("/images")
def post_image(call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    """Road-camera frame (ESP32-CAM). Stored under a server-generated name; the pothole detector (pretrained model, or the labelled heuristic fallback) may raise an UNREVIEWED hazard report."""
    import base64
    import binascii
    import json as _json
    from .. import images
    need_role(call, "road_camera")
    body = parse(ImageIn, call.data)
    edge, progress = body.edge, body.progress
    if edge is None:                                           # locate the frame from the tracked car (camera marker position)
        car = next((s for s in ctx.ev.snapshot() if s["status"] == "active" and s["edge"] and not s["stale"]), None)
        if car is None:
            raise HTTPException(422, "location unknown: no tracked vehicle and no edge supplied")
        edge, progress = car["edge"], car["progress"]
    if edge not in ctx.topology.edges:
        raise HTTPException(422, "unknown edge")
    body.edge, body.progress = edge, progress if progress is not None else 0.5
    try:
        data = base64.b64decode(body.image_b64, validate=True)
        image_id, path = images.store(data, ctx.settings.image_dir)
    except (binascii.Error, images.ImageError) as e:
        raise HTTPException(422, str(e) if isinstance(e, images.ImageError) else "invalid base64")
    detections, reported, det_source = [], None, "camera_heuristic"
    try:
        import cv2
        import numpy as np
        from ..vision import pothole
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        detections, det_source = pothole.detect_labelled(img) if img is not None else ([], det_source)
    except ImportError:
        detections = []                                      # OpenCV not installed: the image is still stored for manual review
    ctx.db.execute("INSERT INTO images(id,device_id,path,bytes,edge,progress,created_at,detection) VALUES(?,?,?,?,?,?,?,?)",
                   (image_id, call.device_id, path, len(data), body.edge, body.progress, now_iso(), _json.dumps(detections[:3])))
    if detections and detections[0]["confidence"] >= 0.5:
        d = detections[0]
        try:
            reported = ctx.hazards.find_active("pothole", body.edge, body.progress)      # same pothole in the next frame: no second report
            if reported is None:
                reported = ctx.hazards.create("pothole", body.edge, d["severity"], det_source, status="reported",
                                              progress=body.progress, confidence=d["confidence"], image_id=image_id)
        except OverflowError:
            reported = None
    images.purge_old(ctx.db, ctx.settings.image_dir)
    return {"image": image_id, "detections": len(detections), "reported_hazard": reported}


@router.get("/sim-commands")
def sim_commands(after: int | None = None, call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    """Scenario world commands for the virtual tabletop. First call (no `after`) only returns the latest cursor."""
    need_role(call, "simulator")
    cmds, latest = ctx.sim_bus.poll(after)
    return {"commands": cmds, "latest": latest}


@router.get("/sim-ev")
def sim_ev(vehicle_id: str, call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    """The simulated driver needs the route it is being guided along (a real driver would read the dashboard)."""
    need_role(call, "simulator")
    for s in ctx.ev.snapshot():
        if s["vehicle_id"] == vehicle_id and s["status"] == "active":
            return {"status": "active", "edge": s["edge"], "progress": s["progress"], "route": s["route"]}
    return {"status": "none"}


@router.post("/command-ack")
def post_ack(call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    need_role(call, "intersection_controller")
    body = parse(AckIn, call.data)
    status = "accepted" if body.accepted else "rejected"
    ctx.db.execute("UPDATE device_commands SET ack_status=?, ack_reason=?, acked_at=? "
                   "WHERE device_id=? AND seq=? AND intersection=?",
                   (status, body.reason, now_iso(), call.device_id, body.seq, body.intersection))
    if not body.accepted:
        event(ctx.db, "command_rejected_by_device", call.device_id, "warning", body.model_dump())
    return {"ok": True}
