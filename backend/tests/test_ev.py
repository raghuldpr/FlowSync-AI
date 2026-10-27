import pytest

from app.core.ev_warnings import FORBIDDEN_PHRASES, LIVE, TEMPLATES, render


@pytest.fixture
def ev(ctx, clock):
    """EV manager with fresh, empty traffic and one declared EV (ext_W -> ext_S)."""
    def fresh(**q):
        for a in ctx.topology.approaches:
            ctx.store.set_traffic(a, q.get(a, 0), "simulated")
    fresh()
    ctx.ev.fresh = fresh
    ctx.ev.session = ctx.ev.declare("EV-1", "ext_W", "ext_S", "operator", "test")
    return ctx.ev


def states(ctx):
    return {(r["approach"], r["state"]) for r in ctx.db.query("SELECT approach,state FROM ev_warnings")}


def live(ev):
    return {a: w["state"] for a, w in ev.session.warnings.items()}


def test_warnings_cover_only_approaches_ahead_in_window(ev, ctx):
    assert ev.position("EV-1", "W1", 0.2)
    assert live(ev) == {"W1": "active", "L13": "active"}        # L12/L23 are not on this route
    assert ev.public_warning("L13")["eta_band"] == "imminent"
    assert ev.public_warning("L12") is None and ev.public_warning("E2") is None


def test_public_warning_is_redacted_template(ev):
    ev.position("EV-1", "W1", 0.2)
    w = ev.public_warning("W1")
    assert set(w) == {"text", "eta_band"}
    assert w["text"] == render("approaching", origin="the West")
    assert ev.public_warning("L13")["text"] == render("approaching", origin="Intersection 1")


def test_passing_cancels_warning_for_passed_approach(ev, ctx):
    ev.position("EV-1", "W1", 0.9)
    ev.position("EV-1", "L13", 0.1)
    assert live(ev) == {"L13": "active"}
    assert ("W1", "cancelled_passed") in states(ctx)


def test_congestion_reroute_redirects_and_cancels(ev, ctx, clock):
    ev.position("EV-1", "W1", 0.2)
    ev.fresh(L13=6)
    ev.tick()
    assert ev.session.route == ["W1", "L12", "L23", "O3"] and ev.session.route_version == 1
    assert live(ev) == {"W1": "active", "L12": "redirected", "L23": "redirected"}
    assert ("L13", "cancelled_route_change") in states(ctx)
    assert ctx.db.query("SELECT * FROM system_events WHERE type='ev_reroute'")


def test_hysteresis_blocks_marginal_improvement(ev):
    ev.position("EV-1", "W1", 0.2)
    ev.fresh(L13=2)                    # current rest 15 s vs alternative 15 s: not >15% better
    ev.tick()
    assert ev.session.route == ["W1", "L13", "O3"] and ev.session.route_version == 0


def test_min_reroute_interval(ev, clock):
    ev.position("EV-1", "W1", 0.2)
    ev.fresh(L13=6)
    ev.tick()
    assert ev.session.route_version == 1
    ev.fresh(L13=0, L23=6)             # now the original is far better ...
    ev.tick()
    assert ev.session.route_version == 1, "... but flip-flopping inside the interval is blocked"
    clock.t[0] += 6
    ev.fresh(L13=0, L23=6)
    ev.position("EV-1", "W1", 0.3)      # keep the location fresh (TTL is 4 s)
    assert ev.session.route_version == 2 and ev.session.route == ["W1", "L13", "O3"]


def test_closure_reroutes_immediately_regardless_of_interval(ev, ctx):
    ev.position("EV-1", "W1", 0.2)
    ev.fresh(L13=6)
    ev.tick()                          # reroute #1 (congestion)
    ctx.store.closed_edges.add("L12")
    ev.fresh()
    ev.tick()                          # route now crosses a closed edge
    assert ev.session.route == ["W1", "L13", "O3"] and ev.session.route_version == 2
    assert any('"closure"' in r["payload"] for r in ctx.db.query("SELECT payload FROM system_events WHERE type='ev_reroute'"))


def test_deviation_reroutes_from_actual_position(ev):
    ev.position("EV-1", "W1", 0.9)
    ev.position("EV-1", "L12", 0.1)    # car took the other way unexpectedly
    assert ev.session.route == ["L12", "L23", "O3"] and ev.session.route_version == 1


def test_stale_location_cancels_warnings_and_priority_then_recovers(ev, ctx, clock):
    ev.position("EV-1", "W1", 0.2)
    assert ev.priority_approaches() == {"W1", "L13"}
    clock.t[0] += 4.5
    ev.fresh()
    ev.tick()
    assert ev.session.stale and live(ev) == {} and ev.priority_approaches() == set()
    assert ev.public_warning("W1") is None
    assert ("W1", "cancelled_stale") in states(ctx)
    assert ctx.db.query("SELECT * FROM system_events WHERE type='ev_location_stale'")
    ev.position("EV-1", "W1", 0.3)
    assert not ev.session.stale and "W1" in live(ev)
    assert ctx.db.query("SELECT * FROM system_events WHERE type='ev_location_recovered'")


def test_priority_follows_position(ev):
    ev.position("EV-1", "W1", 0.2)
    assert ev.priority_approaches() == {"W1", "L13"}, "the next approach is asked to clear ~3 s before the EV reaches it"
    ev.position("EV-1", "W1", 0.6)
    assert ev.priority_approaches() == {"W1", "L13"}
    ev.position("EV-1", "L13", 0.3)
    assert ev.priority_approaches() == {"L13"}


def test_corridor_lead_grows_with_the_queue_in_front_of_the_ev(ev, ctx):
    ctx.store.set_traffic("L13", 6, "simulated")                 # congestion: the EV is routed via I2
    ev.fresh(L13=6)
    ev.tick()
    s = ev.session
    ev.position("EV-1", "W1", 0.0)
    assert s.route == ["W1", "L12", "L23", "O3"]
    assert ev.priority_approaches() == {"W1", "L12"}              # L23 is 10 s away; empty-queue lead is 9 s
    ctx.store.set_traffic("L23", 3, "simulated")                  # three vehicles ahead of the EV there: needs 4.2 s more
    assert ctx.ev.lead_time("L23") == pytest.approx(9 + 1.4 * 3) and "L23" in ev.priority_approaches()


def test_evaluation_switches_turn_each_benefit_off(ev, ctx):
    ev.position("EV-1", "W1", 0.2)
    ev.priority_enabled = False
    assert ev.priority_approaches() == set()
    ev.priority_enabled = True
    ctx.store.set_traffic("L13", 6, "simulated")
    ev.reroute_enabled = False
    ev.fresh(L13=6)
    ev.tick()
    assert ev.session.route == ["W1", "L13", "O3"] and ev.session.route_version == 0, "no live rerouting in the baseline"
    s2 = ev.declare("EV-2", "ext_W", "ext_S", "operator", "t")
    assert s2.route == ["W1", "L13", "O3"], "baseline route ignores live congestion"


def test_arrival_expires_everything(ev, ctx):
    ev.position("EV-1", "W1", 0.2)
    ev.position("EV-1", "L13", 0.5)
    ev.position("EV-1", "O3", 0.95)
    assert ev.session.status == "ended" and ev.priority_approaches() == set()
    assert not any(s in LIVE for _, s in states(ctx))
    row = ctx.db.one("SELECT end_reason FROM ev_sessions")
    assert row["end_reason"] == "arrived"


def test_operator_clear_expires_warnings(ev, ctx):
    ev.position("EV-1", "W1", 0.2)
    assert ev.end(ev.session.id, "operator_cleared")
    assert {s for _, s in states(ctx)} == {"expired"} and ev.public_warning("W1") is None


def test_untracked_vehicle_position_is_discarded(ev, ctx):
    assert ev.position("GHOST-9", "W1", 0.5) is False
    assert ctx.db.query("SELECT * FROM ev_positions") == []


def test_positions_persisted_only_on_edge_change(ev, ctx):
    for p in (0.1, 0.3, 0.5, 0.7):
        ev.position("EV-1", "W1", p)
    assert len(ctx.db.query("SELECT * FROM ev_positions")) == 1
    ev.position("EV-1", "L13", 0.1)
    assert len(ctx.db.query("SELECT * FROM ev_positions")) == 2


def test_one_active_session_per_vehicle(ev):
    with pytest.raises(ValueError):
        ev.declare("EV-1", "ext_W", "ext_S", "operator", "test")
    with pytest.raises(ValueError):
        ev.declare("EV-2", "ext_W", "I1_nope", "operator", "test")


def test_every_transition_is_logged_consistently(ev, ctx):
    ev.position("EV-1", "W1", 0.2)
    ev.fresh(L13=6); ev.tick()
    ev.position("EV-1", "L12", 0.2)
    ev.position("EV-1", "L23", 0.5)
    ev.position("EV-1", "O3", 0.95)
    for w in ctx.db.query("SELECT id,state FROM ev_warnings"):
        evs = ctx.db.query("SELECT * FROM ev_warning_events WHERE warning_id=? ORDER BY id", (w["id"],))
        assert evs[0]["from_state"] is None
        for prev, cur in zip(evs, evs[1:]):
            assert cur["from_state"] == prev["to_state"]
        assert evs[-1]["to_state"] == w["state"]


def test_driver_text_is_fixed_and_lawful():
    for name, t in TEMPLATES.items():
        low = t.lower()
        if "make way" in low:                      # every instruction to drivers carries the lawful qualifier
            assert "safe and lawful" in low, name
        assert not any(p in low for p in FORBIDDEN_PHRASES), name
