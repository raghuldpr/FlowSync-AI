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
