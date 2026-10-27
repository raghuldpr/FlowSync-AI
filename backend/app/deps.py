import json
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from . import auth
from .audit import audit
from .db import now_iso
from .security import AuthError


def get_ctx(request: Request):
    return request.app.state.ctx


def client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


ROLE_ORDER = {"viewer": 1, "operator": 2, "admin": 3}


def _require(min_role: str):
    """Backend-enforced role check; every denial is audit-logged. viewer < operator < admin."""
    def dep(request: Request, ctx=Depends(get_ctx)) -> dict:
        header = request.headers.get("authorization", "")
        sess = auth.resolve(ctx.db, header[7:]) if header.lower().startswith("bearer ") else None
        action = f"{request.method} {request.url.path}"
        if sess is None:
            audit(ctx.db, client_id(request), "public", action, result="denied_unauthenticated")
            raise HTTPException(401, "login required")
        if ROLE_ORDER.get(sess["role"], 0) < ROLE_ORDER[min_role]:
            audit(ctx.db, sess["username"], sess["role"], action, result="denied_role")
            raise HTTPException(403, f"{min_role} role required")
        return sess
    return dep


require_viewer = _require("viewer")        # read-only access to operational data
require_operator = _require("operator")    # anything that changes the system
require_admin = _require("admin")          # users, exports, retention

@dataclass
class DeviceCall:
    device_id: str
    role: str
    spec: dict
    data: dict


async def device_call(request: Request, ctx=Depends(get_ctx)) -> DeviceCall:
    body = await request.body()
    try:
        device_id, spec = ctx.device_auth.verify(request.method, request.url.path, request.headers, body)
    except AuthError as e:
        audit(ctx.db, request.headers.get("x-device-id", "unknown")[:64], "device",
              f"{request.method} {request.url.path}", result=f"denied_{e.detail}")
        raise HTTPException(e.status, e.detail)
    try:
        data = json.loads(body) if body else {}
    except ValueError:
        raise HTTPException(400, "invalid_json")
    ctx.db.execute("INSERT INTO device_status(device_id,role,last_seen) VALUES(?,?,?) "
                   "ON CONFLICT(device_id) DO UPDATE SET last_seen=excluded.last_seen",
                   (device_id, spec["role"], now_iso()))
    ctx.device_seen[device_id] = ctx.store.clock()
    return DeviceCall(device_id, spec["role"], spec, data)


def need_role(call: DeviceCall, *roles: str):
    if call.role not in roles:
        raise HTTPException(403, "device_role_not_permitted")
