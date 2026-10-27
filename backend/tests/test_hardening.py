"""Roles, admin tools, retention, health, metrics, request guards, instant EV alerts, replay."""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import main as app_main, retention
from app.metrics import Metrics
from simulator.tabletop import DeviceClient


def login(client, user, pw):
    r = client.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["role"]


@pytest.fixture
def heads(client):
    out = {}
    for user, pw in (("viewer", "viewer-dev"), ("operator", "test-operator-pw"), ("admin", "admin-dev")):
        out[user], role = login(client, user, pw) if user != "operator" else login(client, "operator", "test-operator-pw")
        assert role == user
    return out


VIEWER_GETS = ["/api/operator/overview", "/api/operator/traffic", "/api/operator/signals", "/api/operator/decisions", "/api/operator/events",
               "/api/operator/audit", "/api/operator/devices", "/api/operator/incidents", "/api/operator/hazards", "/api/operator/ev",
               "/api/operator/proposals", "/api/operator/road-users", "/api/operator/scenarios", "/api/operator/scenario",
               "/api/operator/provenance", "/api/operator/metrics", "/api/operator/health", "/api/operator/replays", "/api/llm/status"]
OPERATOR_POSTS = [("/api/operator/ev", {"vehicle_id": "EV-1"}), ("/api/operator/edges/L13/closed", {"closed": True}),
                  ("/api/operator/incidents", {"type": "stalled_vehicle", "approach": "L13"}),
                  ("/api/operator/hazards", {"kind": "pothole", "edge": "L13", "severity": "low"}),
                  ("/api/operator/scenario/start", {"id": "drill_llm_down"}), ("/api/operator/scenario/stop", None),
                  ("/api/llm/query", {"question": "status?"}), ("/api/operator/emergencies", {"approach": "W1"}),
                  ("/api/operator/proposals/1/confirm", None), ("/api/operator/hazards/1/verify", None)]
ADMIN_ENDPOINTS = [("GET", "/api/admin/users"), ("GET", "/api/admin/audit.csv"), ("POST", "/api/admin/retention/run")]


@pytest.mark.parametrize("path", VIEWER_GETS)
def test_viewer_can_read_everything_operational(client, heads, path):
    assert client.get(path, headers=heads["viewer"]).status_code == 200


@pytest.mark.parametrize("path,body", OPERATOR_POSTS)
def test_viewer_cannot_change_anything_and_every_denial_is_logged(client, ctx, heads, path, body):
    r = client.post(path, json=body, headers=heads["viewer"]) if body is not None else client.post(path, headers=heads["viewer"])
    assert r.status_code == 403
    denial = ctx.db.query("SELECT * FROM audit_log WHERE result='denied_role' AND action=?", (f"POST {path}",))
    assert denial and denial[-1]["actor"] == "viewer" and denial[-1]["role"] == "viewer"


@pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
def test_admin_endpoints_are_admin_only(client, ctx, heads, method, path):
    call = lambda h: client.request(method, path, headers=h)
    assert call({}).status_code == 401
    assert call(heads["viewer"]).status_code == 403 and call(heads["operator"]).status_code == 403
    assert call(heads["admin"]).status_code == 200
    assert ctx.db.query("SELECT 1 FROM audit_log WHERE result='denied_role' AND actor='operator'")


def test_audit_log_records_the_actual_role_of_the_actor(client, ctx, heads):
    assert client.post("/api/operator/hazards", json={"kind": "debris", "edge": "L12", "severity": "low"}, headers=heads["admin"]).status_code == 200
    assert client.post("/api/operator/hazards", json={"kind": "debris", "edge": "L21", "severity": "low"}, headers=heads["operator"]).status_code == 200
    rows = ctx.db.query("SELECT actor, role FROM audit_log WHERE action='hazard_create' ORDER BY id")
    assert [(r["actor"], r["role"]) for r in rows] == [("admin", "admin"), ("operator", "operator")]
    assert ctx.db.query("SELECT role FROM audit_log WHERE action='login' ORDER BY id")[0]["role"] == "viewer"


def test_admin_users_list_never_exposes_hashes(client, heads):
    users = client.get("/api/admin/users", headers=heads["admin"]).json()
    assert {u["username"]: u["role"] for u in users} == {"admin": "admin", "operator": "operator", "viewer": "viewer"}
    assert "hash" not in json.dumps(users) and "scrypt" not in json.dumps(users)


def test_audit_export_is_csv_neutralises_formulas_and_is_itself_audited(client, ctx, heads):
    ctx.db.execute("INSERT INTO audit_log(ts,actor,role,action,target,params,result) VALUES('t','=HYPERLINK(\"x\")','public','POST /x','@a','+1','denied')")
    r = client.get("/api/admin/audit.csv", headers=heads["admin"])
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["id", "ts", "actor", "role", "action", "target", "params", "result"]
    evil = next(x for x in rows if "HYPERLINK" in x[2])
    assert evil[2].startswith("'=") and evil[5].startswith("'@") and evil[6].startswith("'+"), "formula injection neutralised"
    assert ctx.db.query("SELECT 1 FROM audit_log WHERE action='audit_export' AND actor='admin'")


# ---- retention ---------------------------------------------------------------------------------------------------------
def test_retention_removes_old_telemetry_and_keeps_the_audit_log(client, ctx, heads):
    old = "2000-01-01T00:00:00.000+00:00"
    new = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    for ts in (old, new):
        ctx.db.execute("INSERT INTO traffic_snapshots(ts,source,device_id,approach,count) VALUES(?,?,?,?,?)", (ts, "simulated", "camera-1", "W1", 1))
        ctx.db.execute("INSERT INTO ev_positions(session_id,ts,edge,progress) VALUES(1,?,?,?)", (ts, "W1", 0.1))
        ctx.db.execute("INSERT INTO system_events(ts,type,source,severity) VALUES(?,?,?,?)", (ts, "x", "t", "info"))
    ctx.db.execute("INSERT INTO audit_log(ts,actor,role,action,result) VALUES(?,?,?,?,?)", (old, "a", "operator", "ancient", "ok"))
    ctx.db.execute("INSERT INTO sessions VALUES('h','u','operator','t',1.0)")
    res = client.post("/api/admin/retention/run", headers=heads["admin"]).json()
    assert res["traffic_snapshots"] == 1 and res["ev_positions"] == 1 and res["system_events"] >= 1 and res["sessions"] == 1
    assert ctx.db.one("SELECT COUNT(*) n FROM traffic_snapshots")["n"] == 1 and ctx.db.one("SELECT COUNT(*) n FROM ev_positions")["n"] == 1
    assert ctx.db.one("SELECT COUNT(*) n FROM audit_log WHERE action='ancient'")["n"] == 1, "the audit log is never purged"
    assert ctx.metrics.snapshot()["counters"]["retention_runs"] >= 1


def test_retention_keeps_only_the_latest_replay_recordings(ctx):
    for i in range(8):
        ctx.db.execute("INSERT INTO replay_runs(run_id,scenario_id,title,started_at,frames) VALUES(?,?,?,?,1)", (f"r{i}", "s", "t", f"2026-01-0{i + 1}T00:00:00"))
        ctx.db.execute("INSERT INTO replay_frames VALUES(?,?,?)", (f"r{i}", 1.0, "{}"))
    assert retention.run(ctx)["replay_runs"] == 3
    assert [r["run_id"] for r in ctx.db.query("SELECT run_id FROM replay_runs ORDER BY started_at DESC")] == ["r7", "r6", "r5", "r4", "r3"]
    assert ctx.db.one("SELECT COUNT(*) n FROM replay_frames")["n"] == 5


# ---- health ------------------------------------------------------------------------------------------------------------
def test_health_flags_dev_defaults_missing_devices_and_a_dead_control_loop(client, ctx, clock, heads):
    h = client.get("/api/operator/health", headers=heads["viewer"]).json()
    by = {c["name"]: c for c in h["checks"]}
    assert h["ok"] and by["credentials"]["status"] == "warn" and "development defaults" in by["credentials"]["detail"]
    assert by["control loop"]["status"] == "warn" and by["device intersection-1"]["status"] == "warn"
    ctx.controller.cycle()
    ctx.device_seen["intersection-1"] = clock()
    clock.t[0] += 3
    by = {c["name"]: c for c in client.get("/api/operator/health", headers=heads["viewer"]).json()["checks"]}
    assert by["control loop"]["status"] == "ok" and by["device intersection-1"]["status"] == "ok"
    clock.t[0] += 30
    h = client.get("/api/operator/health", headers=heads["viewer"]).json()
    assert not h["ok"] and {c["name"] for c in h["checks"] if c["status"] == "fail"} >= {"control loop", "device intersection-1"}


# ---- metrics and request guards ----------------------------------------------------------------------------------------
def test_metrics_percentiles():
    m = Metrics()
    for v in range(1, 101):
        m.observe("x", v)
    m.count("c", 3)
    s = m.snapshot()
    assert s["counters"]["c"] == 3 and s["series_ms"]["x"] == {"n": 100, "p50": 50.0, "p95": 95.0, "max": 100.0, "last": 100.0} \
        or (s["series_ms"]["x"]["p95"] in (95.0, 96.0) and s["series_ms"]["x"]["max"] == 100.0)
    with m.timer("t"):
        pass
    assert m.snapshot()["series_ms"]["t"]["n"] == 1


def test_http_metrics_use_route_templates_never_tokens(client, ctx, heads):
    tok = client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"}).json()["token"]
    client.get(f"/api/public/road-users/{tok}")
    keys = " ".join(ctx.metrics.snapshot()["series_ms"])
    assert "GET /api/public/road-users/{token}" in keys and tok not in keys
    assert client.get("/api/operator/metrics", headers=heads["viewer"]).json()["counters"]["http_2xx"] >= 1


def test_oversized_bodies_are_rejected_before_parsing(client):
    r = client.post("/api/auth/login", content=b"x" * 1_100_000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_public_reads_are_rate_limited_per_address(client, monkeypatch):
    monkeypatch.setattr(app_main, "PUBLIC_GET_PER_MIN", 5)
    codes = [client.get("/api/health").status_code for _ in range(3)]
    assert codes == [200] * 3, "non-public paths are not counted"
    codes = [client.get("/api/public/topology").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5 and set(codes[5:]) == {429}


# ---- instant EV alerts --------------------------------------------------------------------------------------------------
def test_an_ev_position_report_updates_road_user_alerts_in_the_same_request(client, ctx, clock):
    for a in ctx.topology.approaches:
        ctx.store.set_traffic(a, 0, "simulated")
    u = ctx.roadusers.join("ext_W", "ext_S")
    u.edge, u.progress = "W1", 0.7
    ctx.ev.declare("EV-1", "ext_W", "ext_S", "operator", "t")
    cam = DeviceClient(client, "camera-1", ctx.device_specs["camera-1"]["key"])
    assert not u.alerts
    assert cam.request("POST", "/api/device/ev-position", {"vehicle_id": "EV-1", "edge": "W1", "progress": 0.2}).json() == {"tracked": True}
    assert any(a["kind"] == "ev" and a["state"] == "active" for a in u.alerts.values()), "no waiting for the next 1 s tick"
    s = ctx.metrics.snapshot()["series_ms"]["ev_position_to_alert_ms"]
    assert s["n"] == 1 and s["max"] < 250, "position in -> alerts out is a few milliseconds of server time"


# ---- replay ---------------------------------------------------------------------------------------------------------------
def test_finished_runs_are_recorded_and_can_be_replayed(tmp_path):
    from test_scenarios import Rig
    rig = Rig(tmp_path)
    run = rig.run_scenario("drill_camera_loss")
    h = rig.hdr()
    runs = rig.client.get("/api/operator/replays", headers=h).json()
    assert runs[0]["run_id"] == run.run_id and runs[0]["frames"] >= run.scn.duration - 2
    rec = rig.client.get(f"/api/operator/replays/{run.run_id}", headers=h).json()
    ts = [f["t"] for f in rec["frames"]]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)
    f = rec["frames"][10]
    assert set(f) >= {"traffic", "signals", "ev", "incidents", "hazards", "closed_edges", "step", "events"}
    assert set(f["signals"]) == {"I1", "I2", "I3"} and f["signals"]["I1"]["reported"]["lamps"]["A"] in "RYG"
    assert any(fr["traffic"] and all(v["stale"] for v in fr["traffic"].values()) for fr in rec["frames"]), "the camera-loss drill shows stale readings"
    assert any("Camera feed dies" in e for fr in rec["frames"] for e in fr["events"])
    assert rig.client.get("/api/operator/replays/nope", headers=h).status_code == 404
    assert rig.client.get(f"/api/operator/replays/{run.run_id}").status_code == 401
