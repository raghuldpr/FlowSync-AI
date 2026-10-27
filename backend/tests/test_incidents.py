import pytest

CAP = 8


@pytest.fixture
def inc(ctx):
    def feed(**counts):
        for a in ctx.topology.approaches:
            ctx.store.set_traffic(a, counts.get(a, 0), "simulated")
    inc = ctx.incidents
    inc.feed = feed
    return inc


def open_types(ctx):
    return {(r["type"], r["approach"]) for r in ctx.db.query("SELECT * FROM incidents WHERE status!='resolved'")}


def run(inc, clock, seconds, **counts):
    for _ in range(seconds):
        clock.t[0] += 1
        inc.feed(**counts)
        inc.tick()


def test_congestion_needs_persistence_then_opens_once(inc, ctx, clock):
    run(inc, clock, 9, L12=7)                      # 7/8 = 0.875 >= 0.75 but only 9 s
    assert open_types(ctx) == set()
    run(inc, clock, 3, L12=7)
    assert open_types(ctx) == {("congestion_spike", "L12")}
    run(inc, clock, 30, L12=7)
    assert len(ctx.db.query("SELECT * FROM incidents")) == 1, "no duplicate while open"
    ev = ctx.db.query("SELECT * FROM system_events WHERE type='incident_opened'")
    assert len(ev) == 1 and ev[0]["severity"] == "warning"


def test_flicker_resets_hold_timer(inc, ctx, clock):
    for _ in range(4):
        run(inc, clock, 8, L12=7)
        run(inc, clock, 1, L12=2)
    assert open_types(ctx) == set()


def test_congestion_auto_clears_with_hysteresis(inc, ctx, clock):
    run(inc, clock, 12, L12=7)
    run(inc, clock, 20, L12=5)                     # 0.625: between thresholds -> stays open
    assert open_types(ctx)
    run(inc, clock, 4, L12=2)
    assert open_types(ctx), "needs clear_hold_s below the clear threshold"
    run(inc, clock, 3, L12=2)
    assert open_types(ctx) == set()
    r = ctx.db.one("SELECT * FROM incidents")
    assert r["status"] == "resolved" and r["resolved_by"] == "auto" and r["resolve_reason"] == "occupancy_cleared"


def test_stale_data_never_opens_or_holds_state(inc, ctx, clock):
    inc.feed(L12=8)
    clock.t[0] += 9
    inc.tick()                                     # data is now stale (older than stale_after_s)
    clock.t[0] += 5
    inc.tick()
    assert open_types(ctx) == set()


def green(ctx, inter, axis, elapsed_ms):
    ctx.store.set_actual(inter, {"phase": "GREEN", "axis": axis, "next_axis": axis, "elapsed_ms": elapsed_ms,
                                 "lamps": {"A": "R", "B": "R"}, "backend_ok": True}, "intersection-1")


def feed_stall(ctx, approach, count=2, stationary=12.0, **others):
    for a in ctx.topology.approaches:
        ctx.store.set_traffic(a, others.get(a, 0), "simulated")
    ctx.store.set_traffic(approach, count, "simulated", stationary)


def test_stall_requires_green_long_enough_and_stationary(inc, ctx):
    # L13 is axis A at I3
    feed_stall(ctx, "L13")
    green(ctx, "I3", "B", 15000)                   # wrong axis green: queue at red is not a stall
    inc.tick(); assert open_types(ctx) == set()
    green(ctx, "I3", "A", 4000)                    # green only 4 s: not long enough
    inc.tick(); assert open_types(ctx) == set()
    feed_stall(ctx, "L13", stationary=3.0)         # moving recently
    green(ctx, "I3", "A", 15000)
    inc.tick(); assert open_types(ctx) == set()
    feed_stall(ctx, "L13", stationary=12.0)
    inc.tick()
    assert open_types(ctx) == {("stalled_vehicle", "L13")}
    assert "L13" in ctx.store.incident_closed and "L13" in ctx.store.all_closed()


def test_stall_not_declared_when_downstream_is_full(inc, ctx):
    feed_stall(ctx, "L13", L32=7)                  # L13 discharges into L32 which is >=80% full: spillback
    green(ctx, "I3", "A", 15000)
    inc.tick()
    assert open_types(ctx) == set()


def test_detected_stall_auto_resolves_when_object_moves_and_reopens_routing(inc, ctx):
    feed_stall(ctx, "L13"); green(ctx, "I3", "A", 15000)
    inc.tick()
    assert "L13" in ctx.store.incident_closed
    feed_stall(ctx, "L13", count=2, stationary=0.5)
    inc.tick()
    assert open_types(ctx) == set() and "L13" not in ctx.store.incident_closed


def test_impact_of_stalled_vehicle_on_ring_link(inc, ctx):
    feed_stall(ctx, "L13"); green(ctx, "I3", "A", 15000)
    inc.tick()
    imp = inc.impact("stalled_vehicle", "L13")
    assert imp["affected_edges"][0] == "L13" and set(imp["affected_intersections"]) >= {"I3"}
    assert imp["routes_affected"] >= 1 and imp["unreachable_pairs"] == []
    p = next(x for x in imp["pairs"] if (x["from"], x["to"]) == ("ext_W", "ext_S"))
    assert p["base_path"] == ["W1", "L13", "O3"] and p["alt_path"] == ["W1", "L12", "L23", "O3"]
    assert p["rerouted"] and p["added_delay_s"] == pytest.approx(6.0)
    assert imp["max_added_delay_s"] >= 6.0


def test_impact_of_congestion_is_extra_delay_only(inc, ctx):
    inc.feed(L13=2)                                 # 2 queued * 3 s = +6 s on L13; alternative costs +6 as well
    imp = inc.impact("congestion_spike", "L13")
    p = next(x for x in imp["pairs"] if (x["from"], x["to"]) == ("ext_W", "ext_S"))
    assert p["base_path"] == ["W1", "L13", "O3"] and p["added_delay_s"] == pytest.approx(6.0)


def test_stalled_on_external_entry_makes_pairs_unreachable(inc, ctx):
    ctx.incidents.create("stalled_vehicle", "W1", "operator")
    imp = inc.impact("stalled_vehicle", "W1")
    assert "ext_W->ext_S" in imp["unreachable_pairs"]
    assert all(p["alt_path"] is None for p in imp["pairs"])


def test_stalled_incident_reroutes_active_ev(inc, ctx, clock):
    inc.feed()
    ev = ctx.ev
    s = ev.declare("EV-1", "ext_W", "ext_S", "operator", "t")
    ev.position("EV-1", "W1", 0.2)
    assert s.route == ["W1", "L13", "O3"]
    inc.create("stalled_vehicle", "L13", "operator")
    ev.position("EV-1", "W1", 0.3)
    assert s.route == ["W1", "L12", "L23", "O3"]
    assert any('"closure"' in r["payload"] for r in ctx.db.query("SELECT payload FROM system_events WHERE type='ev_reroute'"))


def test_lifecycle_ack_resolve_and_operator_creation(inc, ctx):
    iid = inc.create("congestion_spike", "L21", "operator")
    assert inc.create("congestion_spike", "L21", "operator") is None
    assert inc.unacknowledged() == 1
    assert inc.acknowledge(iid, "operator") and not inc.acknowledge(iid, "operator")
    assert inc.unacknowledged() == 0
    assert inc.resolve(iid, "operator", "operator_resolved") and not inc.resolve(iid, "operator", "x")
    r = ctx.db.one("SELECT * FROM incidents WHERE id=?", (iid,))
    assert (r["status"], r["ack_by"], r["resolved_by"]) == ("resolved", "operator", "operator")


def test_incident_api_is_operator_only_and_audited(client, ctx, op_headers):
    body = {"type": "stalled_vehicle", "approach": "L13"}
    assert client.get("/api/operator/incidents").status_code == 401
    assert client.get("/api/operator/overview").status_code == 401
    assert client.post("/api/operator/incidents", json=body).status_code == 401
    r = client.post("/api/operator/incidents", json=body, headers=op_headers)
    assert r.status_code == 200
    iid = r.json()["id"]
    assert client.post("/api/operator/incidents", json=body, headers=op_headers).status_code == 409
    assert client.post("/api/operator/incidents", json={"type": "x", "approach": "L13"}, headers=op_headers).status_code == 422
    assert client.post(f"/api/operator/incidents/{iid}/ack").status_code == 401
    assert client.post(f"/api/operator/incidents/{iid}/ack", headers=op_headers).status_code == 200
    assert client.post(f"/api/operator/incidents/{iid}/ack", headers=op_headers).status_code == 409
    lst = client.get("/api/operator/incidents", params={"status": "active"}, headers=op_headers).json()
    assert lst[0]["impact"]["routes_affected"] >= 1 and lst[0]["status"] == "acknowledged"
    assert client.post(f"/api/operator/incidents/{iid}/resolve", headers=op_headers).status_code == 200
    assert client.post(f"/api/operator/incidents/{iid}/resolve", headers=op_headers).status_code == 404
    acts = [a["action"] for a in ctx.db.query("SELECT * FROM audit_log")]
    for a in ("incident_create", "incident_ack", "incident_resolve"):
        assert a in acts
    assert any(a["result"] == "denied_unauthenticated" for a in ctx.db.query("SELECT * FROM audit_log"))


def test_overview_bundles_dashboard_data(client, op_headers):
    o = client.get("/api/operator/overview", headers=op_headers).json()
    assert set(o) >= {"traffic", "signals", "decisions", "ev", "incidents", "alerts_unacknowledged",
                      "closed_edges", "events", "warning_events", "devices"}
    t = client.get("/api/public/topology").json()
    assert set(t["nodes"]) == {"ext_W", "ext_E", "ext_S", "I1", "I2", "I3"} and len(t["edges"]) == 12
    assert "count" not in str(t)
