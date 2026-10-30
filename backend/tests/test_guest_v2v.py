"""Guest roles (vehicle / ambulance), the guest ambulance trip, and the SIMULATED V2V emergency alert."""
import json

import pytest

from app.core import roaduser
from app.core.ev_warnings import FORBIDDEN_PHRASES, LIVE, TEMPLATES

JOIN = "/api/public/road-users"


@pytest.fixture
def world(ctx, clock):
    for a in ctx.topology.approaches:
        ctx.store.set_traffic(a, 0, "simulated")

    class W:
        c, k = ctx, clock

        def tick(self, n=1, dt=0.0):
            for _ in range(n):
                clock.t[0] += dt
                for a in ctx.topology.approaches:
                    ctx.store.set_traffic(a, 0, "simulated")
                ctx.incidents.tick(); ctx.ev.tick(); ctx.roadusers.tick()

        def amb(self, client, origin="ext_W", destination="ext_S"):
            r = client.post(JOIN, json={"role": "ambulance", "origin": origin, "destination": destination})
            assert r.status_code == 200, r.text
            return r.json()

        def car(self, client, edge="W1", progress=0.6, route=("W1", "L13", "O3")):
            r = client.post(JOIN, json={"origin": "ext_W", "destination": "ext_S"})
            assert r.status_code == 200
            u = ctx.roadusers.users[r.json()["token"]]
            u.route, u.edge, u.progress = list(route), edge, progress
            return u

        def state(self, u, kind):
            return {k: a["state"] for k, a in u.alerts.items() if a["kind"] == kind}
    return W()


# ---- 1. guest join with a role --------------------------------------------------------------------------------------
def test_guest_vehicle_joins_with_an_empty_body_and_gets_a_car(client, ctx):
    r = client.post(JOIN, json={})
    assert r.status_code == 200
    v = r.json()
    assert v["role"] == "vehicle" and v["color"] and v["plate"].startswith("FS-") and "ambulance" not in v
    assert v["from"] != v["to"] and ctx.ev.sessions == {}, "a normal car never creates an emergency session"


def test_gate_link_pins_the_entrance(client):
    r = client.post(JOIN, json={"origin": "ext_E"})
    assert r.status_code == 200 and r.json()["from"] == "the East"


def test_unknown_role_is_rejected_and_roles_are_server_side(client, ctx):
    assert client.post(JOIN, json={"role": "admin"}).status_code == 422
    assert client.post(JOIN, json={"role": "operator"}).status_code == 422
    assert client.post(JOIN, json={"role": "vehicle"}).json()["role"] == "vehicle"


# ---- 2. the guest ambulance trip ----------------------------------------------------------------------------------
def test_guest_ambulance_starts_a_real_ev_session_without_an_operator(client, ctx, world):
    v = world.amb(client)
    assert v["role"] == "ambulance" and v["id"].startswith("Ambulance ")
    (s,) = ctx.ev.sessions.values()
    assert s.vehicle_id.startswith("GUEST-") and s.status == "active" and s.edge == "W1" and s.route[0] == "W1"
    assert v["ambulance"]["simulated"] is True and v["ambulance"]["eta_s"] > 0
    assert "GUEST" not in json.dumps(v), "the internal vehicle id is not shown"


def test_guest_ambulance_gets_signal_priority_and_is_not_warned_about_itself(client, ctx, world):
    v = world.amb(client)
    world.tick(2, 0.5)
    assert ctx.ev.priority_approaches(), "the same priority corridor an operator-declared EV gets"
    me = client.get(f"{JOIN}/{v['token']}").json()
    assert not [a for a in me["alerts"] if a["kind"] in ("ev", "v2v")]
    assert me["ambulance"]["priority_at"], "the driver can see where signals are being cleared for them"


def test_guest_ambulance_drives_to_the_destination_and_the_session_ends(client, ctx, world):
    v = world.amb(client)
    tok = v["token"]
    u = ctx.roadusers.users[tok]
    world.tick(1, 0)
    world.tick(42, 1)                                        # W1 + L13 + O3 at pace 3 = about 39 s
    assert u.status == "arrived"
    (s,) = ctx.ev.sessions.values()
    assert s.status == "ended"
    rec = client.get(f"{JOIN}/{tok}").json()["receipt"]
    assert rec["trip_s"] > 0 and "deleted" in rec["privacy"]
    world.tick(10, 1)
    assert client.get(f"{JOIN}/{tok}").status_code == 404, "the session is really gone"


def test_leaving_ends_the_ambulance_session(client, ctx, world):
    tok = world.amb(client)["token"]
    assert client.delete(f"{JOIN}/{tok}").status_code == 200
    (s,) = ctx.ev.sessions.values()
    assert s.status == "ended"


def test_only_a_few_guest_ambulances_at_once(client, ctx, monkeypatch):
    monkeypatch.setattr(roaduser, "MAX_GUEST_AMBULANCES", 1)
    assert client.post(JOIN, json={"role": "ambulance"}).status_code == 200
    r = client.post(JOIN, json={"role": "ambulance"})
    assert r.status_code == 503 and "ambulance" in r.json()["detail"]


# ---- 3. simulated V2V -----------------------------------------------------------------------------------------------
def test_a_normal_vehicle_cannot_raise_an_emergency_alert(client, ctx, world):
    tok = client.post(JOIN, json={}).json()["token"]
    r = client.post(f"{JOIN}/{tok}/alert")
    assert r.status_code == 403
    rows = ctx.db.query("SELECT result FROM audit_log WHERE action='v2v_alert'")
    assert rows and rows[-1]["result"] == "denied_role", "denials are audit-logged"
    assert client.post(f"{JOIN}/no-such-token/alert").status_code == 404


def test_alert_reaches_only_vehicles_the_ambulance_will_come_up_behind(client, ctx, world):
    v = world.amb(client)
    ahead = world.car(client, "W1", 0.6)                      # on the ambulance's road, ahead of it
    behind = world.car(client, "W1", 0.0)                     # same road but not ahead of it... starts at 0.0 (EV progress 0.0)
    behind.progress = -0.0
    elsewhere = ctx.roadusers.users[client.post(JOIN, json={"origin": "ext_E", "destination": "ext_W"}).json()["token"]]
    world.tick(1, 0.1)
    r = client.post(f"{JOIN}/{v['token']}/alert")
    assert r.status_code == 200 and r.json()["simulated"] is True and r.json()["reached"] >= 1
    assert world.state(ahead, "v2v") and list(world.state(ahead, "v2v").values())[0] == "active"
    assert world.state(elsewhere, "v2v") == {}, "a car on other roads hears nothing"
    me = ctx.roadusers.view(ahead)
    a = next(a for a in me["alerts"] if a["kind"] == "v2v")
    assert a["simulated"] is True and "simulated V2V" in a["text"] and "safe and lawful" in a["text"]
    assert a["text"] == TEMPLATES["v2v_emergency"].format(origin="the West")


def test_v2v_wording_is_fixed_and_never_encourages_hurrying():
    for key in ("v2v_emergency", "v2v_ended"):
        t = TEMPLATES[key].lower()
        assert not any(p in t for p in FORBIDDEN_PHRASES)
    assert "safe and lawful" in TEMPLATES["v2v_emergency"] and "simulated" in TEMPLATES["v2v_emergency"]


def test_alert_is_rate_limited_per_ambulance(client, ctx, world, clock):
    v = world.amb(client)
    url = f"{JOIN}/{v['token']}/alert"
    assert client.post(url).status_code == 200
    r = client.post(url)
    assert r.status_code == 429 and "wait" in r.json()["detail"]
    me = client.get(f"{JOIN}/{v['token']}").json()["ambulance"]
    assert me["alert_cooldown_s"] > 0 and me["alert_live"] is True
    clock.t[0] += roaduser.V2V_COOLDOWN_S + 1
    assert client.post(url).status_code == 200


def test_v2v_alert_ends_when_the_ambulance_has_passed(client, ctx, world):
    v = world.amb(client)
    car = world.car(client, "L13", 0.5)                       # ahead of the ambulance, on its route
    world.tick(1, 0.1)
    client.post(f"{JOIN}/{v['token']}/alert")
    assert list(world.state(car, "v2v").values()) == ["active"]
    world.tick(1, 0.0)
    amb = ctx.roadusers.users[v["token"]]
    amb.edge, amb.progress = "O3", 0.5                        # the ambulance is now past the car
    world.tick(2, 0.0)
    states = list(world.state(car, "v2v").values())
    assert states and states[0] not in LIVE


def test_trip_receipt_counts_ambulances_the_driver_made_way_for(client, ctx, world):
    v = world.amb(client)
    car = world.car(client, "W1", 0.6)
    world.tick(2, 0.1)
    assert car.made_way, "the infrastructure warning reached this car"
    car.status, car.arrived_at = "arrived", ctx.roadusers.clock()
    rec = ctx.roadusers.view(car)["receipt"]
    assert rec["made_way_for"] == 1 and "deleted" in rec["privacy"]


# ---- 4. the big-screen feed -----------------------------------------------------------------------------------------
def test_feed_needs_login_and_shows_warned_and_not_warned(client, ctx, world, op_headers):
    assert client.get("/api/operator/road-users/feed").status_code == 401
    v = world.amb(client)
    ahead = world.car(client, "W1", 0.6)
    away = ctx.roadusers.users[client.post(JOIN, json={"origin": "ext_E", "destination": "ext_W"}).json()["token"]]
    world.tick(1, 0.1)
    client.post(f"{JOIN}/{v['token']}/alert")
    f = client.get("/api/operator/road-users/feed", headers=op_headers).json()
    assert f["simulated_v2v"] is True and f["total"] == 3 and f["warned"] == 1
    by = {u["id"]: u for u in f["users"]}
    assert by[ahead.id]["warned"] is True and by[away.id]["warned"] is False
    assert [a["id"] for a in f["ambulances"]] == [v["id"]]
    assert f["v2v"] and f["v2v"][-1]["heard"] >= 1
    assert set(by[ahead.id]) >= {"id", "role", "color", "plate", "edge", "progress", "warned"}
    assert "token" not in json.dumps(f), "tokens never leave the owner"
