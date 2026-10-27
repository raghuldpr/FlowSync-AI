"""The A/B harness must keep measuring what it claims to. These are cheap single-seed guards; the full study is `python -m simulator.evaluate`."""
import json

import pytest

import simulator.evaluate as E


@pytest.fixture(scope="module")
def arms():
    out = {}
    for scenario in ("free", "stall"):
        for name, prio, rr in E.ARMS:
            out[(scenario, name)] = E.run_ev(1, prio, rr, scenario)
    return out


def test_every_arm_gets_the_ambulance_to_its_destination_without_unsafe_lamps(arms):
    assert all(r["arrived"] for r in arms.values())


def test_priority_removes_stops_at_red(arms):
    base, prio = arms[("free", "baseline")], arms[("free", "priority only")]
    assert prio["wait_s"] <= base["wait_s"] and prio["time_s"] <= base["time_s"]
    assert prio["stops"] == 0


def test_rerouting_matters_when_a_stalled_vehicle_blocks_the_direct_road(arms):
    base, full = arms[("stall", "baseline")], arms[("stall", "full system")]
    assert base["route"] == ["W1", "L13", "O3"] and full["route"] == ["W1", "L12", "L23", "O3"]
    assert full["time_s"] < 0.75 * base["time_s"], (base["time_s"], full["time_s"])
    assert arms[("stall", "reroute only")]["time_s"] < arms[("stall", "priority only")]["time_s"]


def test_the_baseline_really_has_no_live_knowledge(arms):
    """Switches off: static free-flow route, no priority. If this stops being true the 'benefit' numbers are meaningless."""
    assert arms[("free", "baseline")]["route"] == ["W1", "L13", "O3"]
    assert arms[("stall", "baseline")]["route"] == ["W1", "L13", "O3"], "baseline ignores the incident the system knows about"


def test_signal_trial_reports_finite_delay_and_throughput():
    r = E.run_signals(1, True, E.PATTERNS["balanced demand"], warm=5, measure=30)
    assert r["served"] > 0 and 0 < r["delay_s"] < 120 and r["peak"] >= 1
    f = E.run_signals(1, False, E.PATTERNS["balanced demand"], warm=5, measure=30)
    assert f["served"] > 0


def test_statistics_and_markdown_are_well_formed():
    assert E.ms([1.0, 3.0]) == (2.0, pytest.approx(1.41421356, rel=1e-6)) and E.ms([5.0]) == (5.0, 0.0)
    rows = {"baseline": {"time": (60.0, 2.0), "wait": (5.0, 1.0), "stops": (1.0, 0.0), "arrived": 3, "n": 3, "route": ["W1"], "times": [60, 60, 60]},
            "full system": {"time": (50.0, 2.0), "wait": (0.0, 0.0), "stops": (0.0, 0.0), "arrived": 3, "n": 3, "route": ["W1"], "times": [50, 50, 50],
                            "paired_saving_pct": (17.0, 1.0)}}
    sig = {"adaptive": {"mean_queue": (3.0, 0.1), "delay_s": (8.0, 1.0), "served": (150.0, 5.0), "paired_delay_reduction_pct": (20.0, 5.0), "delays": [8]},
           "fixed-time": {"mean_queue": (4.0, 0.1), "delay_s": (10.0, 1.0), "served": (120.0, 5.0), "delays": [10]}}
    md = E.render_markdown({"generated": "x", "seeds": 3, "quick": False, "ev": {"free-flowing roads": rows}, "signals": {"balanced": sig}})
    assert "Everything here is simulated" in md and "17 % ± 1" in md and "+20 % ± 5 (reduction)" in md
    assert "not** a prediction for a real road" in md
    json.dumps({"x": rows})                                   # results must be JSON-serialisable for the dashboard
