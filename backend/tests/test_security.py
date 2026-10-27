import json

import pytest

from app.security import sign
from simulator.tabletop import DeviceClient

TRAFFIC = {"source": "simulated", "readings": [{"approach": "W1", "count": 1}]}


def dc(client, devices, name="camera-1"):
    return DeviceClient(client, name, devices[name]["key"])


def denied(ctx, needle):
    return [r for r in ctx.db.query("SELECT * FROM audit_log") if needle in r["result"]]


def test_valid_signed_request_accepted(client, devices, ctx):
    r = dc(client, devices).request("POST", "/api/device/traffic", TRAFFIC)
    assert r.status_code == 200
    assert ctx.store.traffic["W1"]["count"] == 1


def test_tampered_body_and_wrong_key_rejected(client, devices, ctx):
    d = dc(client, devices)
    d.seq += 1
    body = json.dumps(TRAFFIC).encode()
    sig = sign(d.key, "POST", "/api/device/traffic", d.id, d.boot, d.seq, body)
    h = {"X-Device-Id": d.id, "X-Boot": d.boot, "X-Seq": str(d.seq), "X-Signature": sig}
    assert client.post("/api/device/traffic", content=body.replace(b"1", b"9"), headers=h).status_code == 401
    bad = DeviceClient(client, "camera-1", "wrong-key")
    assert bad.request("POST", "/api/device/traffic", TRAFFIC).status_code == 401
    assert denied(ctx, "bad_signature")


def test_unknown_device_forbidden(client, ctx):
    r = DeviceClient(client, "evil-9", "x").request("POST", "/api/device/traffic", TRAFFIC)
    assert r.status_code == 403
    assert denied(ctx, "unknown_device")


def test_replay_rejected(client, devices, ctx):
    d = dc(client, devices)
    body = json.dumps(TRAFFIC).encode()
    d.seq += 1
    h = {"X-Device-Id": d.id, "X-Boot": d.boot, "X-Seq": str(d.seq),
         "X-Signature": sign(d.key, "POST", "/api/device/traffic", d.id, d.boot, d.seq, body)}
    assert client.post("/api/device/traffic", content=body, headers=h).status_code == 200
    assert client.post("/api/device/traffic", content=body, headers=h).status_code == 401
    assert denied(ctx, "replayed_seq")


def test_reboot_new_boot_ok_but_old_boot_cannot_return(client, devices):
    d = dc(client, devices)
    assert d.request("POST", "/api/device/traffic", TRAFFIC).status_code == 200
    old = d.boot
    d.boot, d.seq = "newboot01", 0                       # device rebooted: seq restarts
    assert d.request("POST", "/api/device/traffic", TRAFFIC).status_code == 200
    d.boot, d.seq = old, 99                               # attacker replays under the old session
    assert d.request("POST", "/api/device/traffic", TRAFFIC).status_code == 401


def test_role_enforced_on_devices(client, devices):
    r = dc(client, devices, "camera-1").request("GET", "/api/device/commands")
    assert r.status_code == 403
    r = dc(client, devices, "car-1").request("POST", "/api/device/traffic", TRAFFIC)
    assert r.status_code == 403


def test_input_validation(client, devices):
    d = dc(client, devices)
    assert d.request("POST", "/api/device/traffic", {**TRAFFIC, "readings": [{"approach": "W1", "count": 100}]}).status_code == 422
    assert d.request("POST", "/api/device/traffic", {**TRAFFIC, "readings": [{"approach": "ZZ", "count": 1}]}).status_code == 422
    assert d.request("POST", "/api/device/traffic", {"source": "magic", "readings": []}).status_code == 422


def test_unsafe_lamps_from_device_raise_critical_event(client, devices, ctx):
    d = dc(client, devices, "intersection-1")
    st = {"phase": "GREEN", "axis": "A", "next_axis": "B", "elapsed_ms": 10, "lamps": {"A": "G", "B": "G"}}
    assert d.request("POST", "/api/device/state", {"intersections": {"I1": st}}).status_code == 200
    ev = ctx.db.query("SELECT * FROM system_events WHERE type='unsafe_lamps_reported'")
    assert ev and ev[0]["severity"] == "critical"


def test_device_cannot_report_other_intersections(client, devices):
    d = dc(client, devices, "intersection-1")
    st = {"phase": "GREEN", "axis": "A", "next_axis": "B", "elapsed_ms": 10, "lamps": {"A": "G", "B": "R"}}
    assert d.request("POST", "/api/device/state", {"intersections": {"I9": st}}).status_code in (403, 422)


def test_ev_endpoints_are_role_separated(client, devices, ctx, op_headers):
    body = {"vehicle_id": "EV-1"}
    assert client.post("/api/operator/ev", json=body).status_code == 401
    assert client.post("/api/operator/edges/L13/closed", json={"closed": True}).status_code == 401
    assert client.post("/api/operator/ev/1/clear").status_code == 401
    # cars may declare via their button; cameras/intersections may not
    assert dc(client, devices, "camera-1").request("POST", "/api/device/ev-declare", body).status_code == 403
    assert dc(client, devices, "intersection-1").request("POST", "/api/device/ev-position",
                                                         {"vehicle_id": "EV-1", "edge": "W1", "progress": .1}).status_code == 403
    r = dc(client, devices, "car-1").request("POST", "/api/device/ev-declare", body)
    assert r.status_code == 200 and r.json()["route"] == ["W1", "L13", "O3"]
    assert dc(client, devices, "car-1").request("POST", "/api/device/ev-declare", body).status_code == 409
    cam = dc(client, devices, "camera-1")
    assert cam.request("POST", "/api/device/ev-position", {"vehicle_id": "EV-1", "edge": "W1", "progress": 0.2}).json() == {"tracked": True}
    assert cam.request("POST", "/api/device/ev-position", {"vehicle_id": "EV-1", "edge": "ZZ", "progress": 0.2}).status_code == 422
    assert cam.request("POST", "/api/device/ev-position", {"vehicle_id": "EV-1", "edge": "W1", "progress": 1.5}).status_code == 422
    assert client.post("/api/operator/edges/L13/closed", json={"closed": True}, headers=op_headers).status_code == 200
    actions = [a["action"] for a in ctx.db.query("SELECT * FROM audit_log")]
    assert "ev_declare" in actions and "edge_closed" in actions


def test_public_warning_endpoint_leaks_nothing(client, devices, ctx):
    dc(client, devices, "car-1").request("POST", "/api/device/ev-declare", {"vehicle_id": "EV-1"})
    dc(client, devices, "camera-1").request("POST", "/api/device/ev-position", {"vehicle_id": "EV-1", "edge": "W1", "progress": 0.2})
    r = client.get("/api/public/warnings", params={"approach": "W1"})
    assert r.status_code == 200
    w = r.json()["warning"]
    assert set(w) == {"text", "eta_band"} and "safe and lawful" in w["text"]
    assert "EV-1" not in r.text and "route" not in r.text and "progress" not in r.text
    assert client.get("/api/public/warnings", params={"approach": "L12"}).json()["warning"] is None
    assert client.get("/api/public/warnings", params={"approach": "nope"}).status_code == 404
    assert client.get("/api/public/warnings").status_code == 422


OPERATOR_GETS = ["/api/operator/ev", "/api/operator/ev/warnings", "/api/operator/traffic", "/api/operator/signals", "/api/operator/decisions",
                 "/api/operator/events", "/api/operator/audit", "/api/operator/devices",
                 "/api/operator/emergencies"]


@pytest.mark.parametrize("path", OPERATOR_GETS)
def test_operator_endpoints_closed_to_public_and_denials_logged(client, ctx, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert denied(ctx, "denied_unauthenticated")


def test_operator_writes_closed_and_open_with_audit(client, ctx, op_headers):
    body = {"approach": "W1", "vehicle_id": "EV-1"}
    assert client.post("/api/operator/emergencies", json=body).status_code == 401
    r = client.post("/api/operator/emergencies", json=body, headers=op_headers)
    assert r.status_code == 200
    eid = r.json()["id"]
    assert client.post(f"/api/operator/emergencies/{eid}/clear").status_code == 401
    assert client.post(f"/api/operator/emergencies/{eid}/clear", headers=op_headers).status_code == 200
    actions = [a["action"] for a in ctx.db.query("SELECT * FROM audit_log")]
    assert "emergency_create" in actions and "emergency_clear" in actions
    assert client.post("/api/operator/emergencies", json={"approach": "nope"}, headers=op_headers).status_code == 422


@pytest.mark.parametrize("path", OPERATOR_GETS)
def test_operator_can_read(client, op_headers, path):
    assert client.get(path, headers=op_headers).status_code == 200


def test_login_failure_audited_and_rate_limited(client, ctx):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "operator", "password": "x"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "operator", "password": "x"}).status_code == 429
    assert denied(ctx, "denied_bad_credentials") and denied(ctx, "denied_rate_limited")


def test_public_views_are_redacted(client, devices, ctx):
    dc(client, devices).request("POST", "/api/device/traffic", TRAFFIC)
    cong = client.get("/api/public/congestion").json()
    assert cong["W1"] == "low" and cong["L12"] == "unknown"
    assert set(cong.values()) <= {"low", "medium", "high", "unknown"}
    assert "source" not in json.dumps(cong) and not any(ch.isdigit() for ch in json.dumps(cong).replace("L12", "").replace("L13", "").replace("L21", "").replace("L23", "").replace("L31", "").replace("L32", "").replace("W1", "").replace("E2", "").replace("S3", ""))
    assert client.get("/api/public/signals").json() == {"I1": "unknown", "I2": "unknown", "I3": "unknown"}
    assert client.get("/api/health").json() == {"status": "ok"}
