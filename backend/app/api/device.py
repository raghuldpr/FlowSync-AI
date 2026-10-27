from typing import Dict, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from ..audit import event
from ..db import now_iso
from ..core.validator import lamps_safe
from ..deps import DeviceCall, device_call, get_ctx, need_role

router = APIRouter(prefix="/api/device", tags=["device"])


class Reading(BaseModel):
    approach: str
    count: int = Field(ge=0, le=64)


class TrafficIn(BaseModel):
    source: Literal["overhead_camera", "recorded_video", "simulated", "ultrasonic"]
    readings: list[Reading] = Field(max_length=32)


class InterState(BaseModel):
    phase: Literal["GREEN", "YELLOW", "ALL_RED"]
    axis: Literal["A", "B"]
    next_axis: Literal["A", "B"]
    elapsed_ms: int = Field(ge=0)
    lamps: Dict[Literal["A", "B"], Literal["R", "Y", "G"]]
    backend_ok: bool = True


class StateIn(BaseModel):
    intersections: Dict[str, InterState]


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
    for r in body.readings:
        ctx.store.set_traffic(r.approach, r.count, body.source)
        ctx.db.execute("INSERT INTO traffic_snapshots(ts,source,device_id,approach,count) VALUES(?,?,?,?,?)",
                       (now_iso(), body.source, call.device_id, r.approach, r.count))
    return {"ok": True}


@router.post("/state")
def post_state(call: DeviceCall = Depends(device_call), ctx=Depends(get_ctx)):
    need_role(call, "intersection_controller")
    body = parse(StateIn, call.data)
    allowed = set(call.spec.get("intersections", []))
    for inter, st in body.intersections.items():
        if inter not in allowed:
            raise HTTPException(403, f"device not responsible for {inter}")
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
