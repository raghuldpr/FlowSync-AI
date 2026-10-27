import pytest

# I1: axis A = L21, L31 (downstream L13 / L12); axis B = W1 (downstream L12, L13)


@pytest.fixture
def setup(ctx):
    def _(green="A", elapsed=6000, phase="GREEN", **counts):
        q = {a: 0 for a in ctx.topology.approaches}
        q.update(counts)
        other = "B" if green == "A" else "A"
        ctx.store.set_actual("I1", {"phase": phase, "axis": green, "next_axis": other, "elapsed_ms": elapsed,
                                    "lamps": {"A": "R", "B": "R"}, "backend_ok": True}, "intersection-1")
        return q
    return _


def decide(ctx, q, ev=frozenset()):
    return ctx.controller.decide("I1", q, set(ev))


def test_switches_to_higher_pressure(ctx, setup):
    d = decide(ctx, setup(L21=1, W1=6))
    assert (d["target"], d["reason"]) == ("B", "higher_pressure")


def test_holds_when_current_is_busier(ctx, setup):
    d = decide(ctx, setup(L21=5, W1=3))
    assert (d["target"], d["reason"]) == ("A", "keep_current")


def test_min_green_blocks_switch(ctx, setup):
    d = decide(ctx, setup(elapsed=2000, L21=0, W1=8))
    assert d["target"] == "A"


def test_stale_inputs_hold_not_zero(ctx, setup):
    q = {a: None for a in ctx.topology.approaches}
    setup()
    d = decide(ctx, q)
    assert (d["target"], d["reason"]) == ("A", "no_fresh_data_hold")


def test_spillback_removes_contribution(ctx, setup):
    # W1 would win 8 vs 0, but L13 downstream is at 7/8 >= 0.8 occupancy
    d = decide(ctx, setup(W1=8, L13=7, L21=0, L31=0))
    assert d["target"] == "A"
    assert "W1:spillback" in d["inputs"]["notes"]


def test_stale_downstream_skips_approach(ctx, setup):
    q = setup(W1=8)
    q["L12"] = None
    d = decide(ctx, q)
    assert "W1:downstream_stale" in d["inputs"]["notes"]
    assert d["target"] == "A"


def test_gridlock_rotation_only_when_both_axes_blocked(ctx, setup):
    jam = dict(L21=5, L31=5, W1=5, L13=7, L12=7)           # every approach feeds a >=80% full link
    d = decide(ctx, setup(elapsed=6000, **jam))
    assert (d["target"], d["reason"]) == ("A", "keep_current"), "no rotation before max green"
    d = decide(ctx, setup(elapsed=21000, **jam))
    assert (d["target"], d["reason"]) == ("B", "gridlock_rotation")
    # B blocked but A flowing (L31 feeds a non-full link): not gridlock, spillback rule still holds B
    d = decide(ctx, setup(elapsed=21000, L21=5, L31=5, W1=5, L13=7, L12=1))
    assert d["reason"] != "gridlock_rotation"


def test_per_approach_capacity_drives_spillback_and_public_bands(ctx, setup, client):
    from dataclasses import replace
    q = setup(W1=5, L13=4, L21=0, L31=0)
    assert decide(ctx, q)["inputs"]["notes"] == []                 # 4/8 = 50%: not spilled back
    ctx.topology.approaches["L13"] = replace(ctx.topology.approaches["L13"], capacity=4)
    d = decide(ctx, q)
    assert "W1:spillback" in d["inputs"]["notes"]                  # 4/4 = 100% of THIS lane's physical capacity
    for a in ctx.topology.approaches:
        ctx.store.set_traffic(a, 4 if a == "L13" else 0, "overhead_camera")
    assert client.get("/api/public/congestion").json()["L13"] == "high"
    assert client.get("/api/public/topology").json()["capacities"]["L13"] == 4


def test_a_lone_vehicle_behind_a_congested_link_is_not_starved(ctx, setup):
    """Found by the A/B evaluation: pressure is clamped at 0, so a single waiting vehicle whose downstream link holds more
    vehicles had zero pressure, and the other axis kept green forever (an ambulance waited 230 s at a stop line)."""
    q = setup(elapsed=6000, L13=6, W1=1)                    # W1's downstream (L13) holds 6 -> pressure max(0, 1-6) = 0
    d = decide(ctx, q)
    assert d["inputs"]["scores"]["B"] == 0 and d["inputs"]["demand"]["B"] == 1
    assert (d["target"], d["reason"]) == ("B", "current_axis_empty"), "nobody to serve on A, someone waiting on B"
    d = decide(ctx, setup(elapsed=21000, L21=2, L13=6, W1=1))   # A still has demand: max green still lets B in
    assert (d["target"], d["reason"]) == ("B", "max_green_reached")
    d = decide(ctx, setup(elapsed=6000, L21=2, L13=0, W1=1))     # normal case unchanged: A has demand and more pressure
    assert d["target"] == "A"


def test_demand_ignores_vehicles_blocked_by_a_full_downstream_link(ctx, setup):
    d = decide(ctx, setup(elapsed=21000, L13=7, W1=3))           # W1's downstream L13 is 87 % full: do not feed it
    assert d["inputs"]["demand"]["B"] == 0 and d["target"] == "A"


def test_max_green_forces_switch(ctx, setup):
    d = decide(ctx, setup(elapsed=21000, L21=8, W1=2))
    assert (d["target"], d["reason"]) == ("B", "max_green_reached")


def test_emergency_priority_and_min_green(ctx, setup):
    d = decide(ctx, setup(elapsed=6000), {"W1"})
    assert (d["target"], d["reason"]) == ("B", "emergency_priority")
    d = decide(ctx, setup(elapsed=1000), {"W1"})
    assert (d["target"], d["reason"]) == ("A", "emergency_waiting_min_green")
    d = decide(ctx, setup(green="B", elapsed=9000), {"W1"})
    assert (d["target"], d["reason"]) == ("B", "emergency_holding_green")


def test_transition_in_progress_keeps_heading(ctx, setup):
    d = decide(ctx, setup(phase="YELLOW", L21=8, W1=0))
    assert (d["target"], d["reason"]) == ("B", "transition_in_progress")


def test_cycle_issues_validated_commands_and_logs_changes(ctx):
    for a in ctx.topology.approaches:
        ctx.store.set_traffic(a, 0, "simulated")
    ctx.controller.cycle()
    cmds = ctx.store.get_commands("intersection-1")
    assert {c["intersection"] for c in cmds} == {"I1", "I2", "I3"}
    assert all(c["seq"] >= 1 and c["valid_for_ms"] == 4000 for c in cmds)
    n = len(ctx.db.query("SELECT * FROM signal_decisions"))
    ctx.controller.cycle()                                   # unchanged decision -> not re-logged
    assert len(ctx.db.query("SELECT * FROM signal_decisions")) == n
    assert ctx.store.get_commands("intersection-1")[0]["seq"] > cmds[0]["seq"]
