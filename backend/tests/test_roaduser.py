"""Road-user layer: relevance (who is warned), alert lifecycle, privacy, rate limits, API."""
import json

import pytest

from app.core import roaduser
from app.core.ev_warnings import FORBIDDEN_PHRASES, LIVE, TEMPLATES

POS = {"W1": 0.2}


@pytest.fixture
def world(ctx, clock):
    for a in ctx.topology.approaches:
        ctx.store.set_traffic(a, 0, "simulated")

    class W:
        c, k = ctx, clock

        def ev(self, edge="W1", progress=0.2):
            if not ctx.ev.sessions:
                ctx.ev.declare("EV-1", "ext_W", "ext_S", "operator", "t")
            ctx.ev.position("EV-1", edge, progress)
            return next(iter(ctx.ev.sessions.values()))

        def user(self, origin, destination, edge=None, progress=0.0):
            u = ctx.roadusers.join(origin, destination)
            if edge:
                u.route = u.route if edge in u.route else [edge] + u.route
                u.edge, u.progress = edge, progress
            return u

        def tick(self, n=1, dt=0.0, keep_ev=None):
            for _ in range(n):
                clock.t[0] += dt
                for a in ctx.topology.approaches:
                    ctx.store.set_traffic(a, ctx.store.traffic[a]["count"], "simulated")
                if keep_ev:
                    ctx.ev.position("EV-1", *keep_ev)
                ctx.incidents.tick(); ctx.ev.tick(); ctx.roadusers.tick()

        def alerts(self, u, kind="ev"):
            return {k: a["state"] for k, a in u.alerts.items() if a["kind"] == kind}
    return W()


def test_join_view_is_private_and_uses_pseudonyms(world, client):
    r = client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"].startswith("Car ") and len(body["token"]) >= 20
    assert body["route_text"] == "the West → Intersection 1 → Intersection 3 → the South"
    text = json.dumps(body)
    assert "EV-1" not in text and "vehicle" not in text, "nothing about emergency vehicles beyond fixed alert text"
    assert set(body) == {"token", "id", "status", "edge", "progress", "from", "to", "route_text", "alerts", "suggestion", "version"}
    me = client.get(f"/api/public/road-users/{body['token']}").json()
    assert me["id"] == body["id"] and me["alerts"] == [] and me["suggestion"] is None


def test_bad_requests_unknown_tokens_and_limits(world, client, ctx, clock):
    ok = [client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"}).status_code for _ in range(10)]
    assert set(ok) == {200}, "10 joins per minute are allowed"
    assert client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"}).status_code == 429
    clock.t[0] += 61
    assert client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"}).status_code == 200
    assert client.post("/api/public/road-users", json={"origin": "I1", "destination": "ext_S"}).status_code == 422
    assert client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_W"}).status_code == 422
    assert client.post("/api/public/road-users", json={"origin": "x" * 40, "destination": "ext_S"}).status_code == 422
    assert client.get("/api/public/road-users/not-a-token").status_code == 404
    assert client.get("/api/public/road-users/" + "a" * 100).status_code == 404


def test_capacity_limit_and_leave(world, client, ctx, monkeypatch):
    monkeypatch.setattr(roaduser, "MAX_USERS", 2)
    t = [client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"}) for _ in range(3)]
    assert [r.status_code for r in t] == [200, 200, 503]
    tok = t[0].json()["token"]
    assert client.delete(f"/api/public/road-users/{tok}").status_code == 200
    assert client.get(f"/api/public/road-users/{tok}").status_code == 404


def test_users_drive_arrive_and_are_forgotten(world, ctx):
    u = world.user("ext_W", "ext_S")
    world.tick(1, 0)
    world.tick(60, 1)                                   # W1 12 s + L13 15 s + O3 12 s at pace 3
    assert u.status == "arrived"
    world.tick(10, 1)
    assert u.token not in ctx.roadusers.users


def test_idle_phone_sessions_expire_but_simulated_ones_do_not(world, ctx):
    a, b = world.user("ext_W", "ext_S"), world.user("ext_E", "ext_W")
    b.simulated = True
    world.tick(1, 0)
    world.tick(1, roaduser.IDLE_TTL_S + 1)
    assert a.token not in ctx.roadusers.users and b.token in ctx.roadusers.users


# ---- relevance: only users the EV will actually come up behind -------------------------------------------------------
def test_only_users_on_the_ambulances_corridor_are_warned(world, ctx):
    ahead = world.user("ext_W", "ext_S", "W1", 0.6)          # same road, EV (progress 0.2) is behind -> relevant
    behind = world.user("ext_W", "ext_S", "W1", 0.05)         # same road but the EV is already ahead of them
    other_way = world.user("ext_E", "ext_W")                   # E2 > L21 > O1: different roads
    opposite = world.user("ext_S", "ext_W")                    # S3 > L31 > O1: L31 is the opposite lane of L13
    later = world.user("ext_W", "ext_S")                       # will enter the EV's roads (starts on W1 at 0.0)
    world.ev("W1", 0.2)
    world.tick(2, 0.0, keep_ev=("W1", 0.2))
    assert world.alerts(ahead) == {f"ev:{next(iter(ctx.ev.sessions))}": "active"}
    assert world.alerts(behind) == {}
    assert world.alerts(other_way) == {} and world.alerts(opposite) == {}
    assert world.alerts(later) == {}, "starting behind the EV on the same road: it is ahead of them, not approaching"


def test_user_far_ahead_on_the_route_is_warned_only_inside_the_horizon(world, ctx):
    from dataclasses import replace
    ctx.topology.ev = replace(ctx.topology.ev, warn_horizon_s=5.0)      # this little map is shorter than the real 20 s window
    near = world.user("ext_W", "ext_S", "L13", 0.1)           # EV reaches L13 in ~3 s
    far = world.user("ext_W", "ext_S", "O3", 0.1)             # EV reaches O3 in ~8 s: outside a 5 s window
    world.ev("W1", 0.2)
    world.tick(2, 0.0, keep_ev=("W1", 0.2))
    assert world.alerts(near) and world.alerts(far) == {}


def test_alert_wording_comes_only_from_the_fixed_templates(world, ctx):
    u = world.user("ext_W", "ext_S", "W1", 0.6)
    world.ev("W1", 0.2)
    world.tick(2, 0.0, keep_ev=("W1", 0.2))
    v = ctx.roadusers.view(u)
    ev = next(a for a in v["alerts"] if a["kind"] == "ev")
    assert ev["text"] == TEMPLATES["approaching"].format(origin="the West") and "safe and lawful" in ev["text"]
    assert set(ev) == {"id", "kind", "state", "text", "band"}
    assert "EV-1" not in json.dumps(v) and "route" not in json.dumps(list(v.keys())).replace("route_text", "")


def test_ev_alert_lifecycle_updated_passed_stale_expired(world, ctx):
    u = world.user("ext_W", "ext_S", "O3", 0.1)               # waiting at the destination end of the EV's route
    u.route = ["O3"]
    world.ev("W1", 0.2)
    key = f"ev:{next(iter(ctx.ev.sessions))}"
    world.tick(2, 0.0, keep_ev=("W1", 0.2))
    assert u.alerts[key]["state"] == "active" and u.alerts[key]["band"] == "soon"
    world.tick(1, 0.0, keep_ev=("W1", 0.85))                  # EV much closer
    assert u.alerts[key]["state"] == "updated" and u.alerts[key]["band"] == "imminent"
    world.tick(1, 0.0, keep_ev=("O3", 0.5))                   # the EV drives past the user's spot
    assert u.alerts[key]["state"] == "cancelled_passed" and "has passed" in u.alerts[key]["text"]
    world.tick(10, 1.0, keep_ev=("O3", 0.95))                 # EV arrives; the user's all-clear is dropped after a few seconds
    assert key not in u.alerts, "all-clear stays visible a few seconds, then is dropped"


def test_stale_ev_location_cancels_user_alerts(world, ctx):
    u = world.user("ext_W", "ext_S", "W1", 0.6)
    world.ev("W1", 0.2)
    key = f"ev:{next(iter(ctx.ev.sessions))}"
    world.tick(2, 0.0, keep_ev=("W1", 0.2))
    assert u.alerts[key]["state"] == "active"
    world.tick(1, 5.0)                                          # no position for 5 s
    assert u.alerts[key]["state"] == "cancelled_stale" and "location is unknown" in u.alerts[key]["text"]


def test_ended_ev_session_gives_all_clear(world, ctx):
    u = world.user("ext_W", "ext_S", "W1", 0.6)
    s = world.ev("W1", 0.2)
    world.tick(2, 0.0, keep_ev=("W1", 0.2))
    ctx.ev.end(s.id, "operator_cleared")
    world.tick(1, 0.0)
    assert world.alerts(u) == {f"ev:{s.id}": "expired"}


def test_route_change_redirects_or_cancels_user_alerts(world, ctx):
    ctx.store.set_traffic("L13", 6, "simulated")                 # congestion: EV goes via I2
    s = world.ev("W1", 0.2)
    assert "L12" in s.route
    u_via_l13 = world.user("ext_W", "ext_S", "L13", 0.1)
    u_via_l12 = world.user("ext_W", "ext_S", "L12", 0.1)
    u_via_l12.route = ["L12", "L23", "O3"]
    u_via_l13.route = ["L13", "O3"]
    world.tick(2, 0.0, keep_ev=("W1", 0.2))
    key = f"ev:{s.id}"
    assert key in u_via_l12.alerts and key not in u_via_l13.alerts
    ctx.store.closed_edges.add("L12")                            # the EV is rerouted via L13
    world.tick(2, 0.0, keep_ev=("W1", 0.25))
    assert ctx.ev.sessions[s.id].route_version == 1
    assert u_via_l12.alerts[key]["state"] == "cancelled_route_change"
    assert u_via_l13.alerts[key]["state"] == "redirected" and "route changed" in u_via_l13.alerts[key]["text"]


# ---- incidents, closures, suggestions --------------------------------------------------------------------------------
def test_incident_ahead_alert_with_delay_then_cleared(world, ctx):
    u = world.user("ext_W", "ext_S")
    ctx.store.set_traffic("L13", 6, "simulated")
    iid = ctx.incidents.create("congestion_spike", "L13", "operator")
    world.tick(2, 0.0)
    a = u.alerts[f"inc:{iid}"]
    assert a["state"] == "active" and "heavy queue" in a["text"] and "Incident ahead on Intersection 1 to Intersection 3" in a["text"]
    assert "extra delay" in a["text"]
    ctx.incidents.resolve(iid, "operator", "test")
    world.tick(1, 0.0)
    assert u.alerts[f"inc:{iid}"]["state"] == "cleared" and "cleared" in u.alerts[f"inc:{iid}"]["text"]


def test_a_user_who_avoids_the_incident_is_not_told_it_was_cleared(world, ctx):
    u = world.user("ext_W", "ext_S")
    iid = ctx.incidents.create("stalled_vehicle", "L13", "operator")      # closes L13: the user is rerouted around it
    world.tick(2, 0.0)
    assert u.route == ["W1", "L12", "L23", "O3"] and f"inc:{iid}" not in u.alerts
    assert u.alerts["route"]["state"] == "active"


def test_closed_road_ahead_forces_a_reroute_with_notice(world, ctx):
    u = world.user("ext_W", "ext_S")
    assert u.route == ["W1", "L13", "O3"]
    ctx.incidents.create("stalled_vehicle", "L13", "operator")    # closes L13 for routing
    world.tick(2, 0.0)
    assert u.route == ["W1", "L12", "L23", "O3"]
    assert u.alerts["route"]["state"] == "active" and "road ahead is closed" in u.alerts["route"]["text"]
    world.tick(14, 1.0)
    assert "route" not in u.alerts


def test_congestion_gives_a_suggestion_the_user_can_accept(world, ctx, client):
    tok = client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"}).json()["token"]
    ctx.store.set_traffic("L13", 6, "simulated")
    world.tick(2, 0.0)
    v = client.get(f"/api/public/road-users/{tok}").json()
    assert v["suggestion"] and "Suggested alternative route" in v["suggestion"]["text"] and "Intersection 2" in v["suggestion"]["text"]
    assert client.post(f"/api/public/road-users/{tok}/route/accept").json()["route_text"].count("Intersection 2") == 1
    assert client.post(f"/api/public/road-users/{tok}/route/accept").status_code == 409


def test_suggestion_needs_a_real_gain(world, ctx):
    u = world.user("ext_W", "ext_S")
    ctx.store.set_traffic("L13", 2, "simulated")                  # +6 s on L13, alternative costs the same: no suggestion
    world.tick(2, 0.0)
    assert u.suggestion is None


# ---- long-poll push, operator view, provenance ----------------------------------------------------------------------
def test_long_poll_returns_immediately_on_change_and_after_timeout_otherwise(world, client, ctx):
    tok = client.post("/api/public/road-users", json={"origin": "ext_W", "destination": "ext_S"}).json()
    v0 = tok["version"]
    same = client.get(f"/api/public/road-users/{tok['token']}/poll", params={"after": v0, "timeout": 0.3})
    assert same.status_code == 200 and same.json()["version"] == v0
    stale = client.get(f"/api/public/road-users/{tok['token']}/poll", params={"after": v0 - 5, "timeout": 5})
    assert stale.status_code == 200
    assert client.get(f"/api/public/road-users/{tok['token']}/poll", params={"timeout": 99}).status_code == 422


def test_operator_sees_pseudonyms_and_states_but_never_tokens(world, client, ctx, op_headers):
    u = world.user("ext_W", "ext_S", "W1", 0.6)
    world.ev("W1", 0.2)
    world.tick(2, 0.0, keep_ev=("W1", 0.2))
    assert client.get("/api/operator/road-users").status_code == 401
    rows = client.get("/api/operator/road-users", headers=op_headers).json()
    assert rows[0]["id"] == u.id and "active" in rows[0]["alerts"].values() and u.token not in json.dumps(rows)
    prov = {r["component"]: r for r in client.get("/api/operator/provenance", headers=op_headers).json()}
    assert prov["Road-user alerts (phones)"]["status"] == "Built" and prov["Road-user positions"]["status"] == "Simulated"


def test_every_template_avoids_unsafe_wording_and_ev_instructions_say_safe_and_lawful():
    for name, t in TEMPLATES.items():
        low = t.lower()
        assert not any(p in low for p in FORBIDDEN_PHRASES), name
        if "make way" in low:
            assert "safe and lawful" in low, name
