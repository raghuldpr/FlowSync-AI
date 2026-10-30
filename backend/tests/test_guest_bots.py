"""The rehearsal bots (simulator/guest_bots.py) against the real app, in-process. Time is a fake clock advanced by the bots' step()."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from simulator.guest_bots import Bots, Check, main  # noqa: E402


@pytest.fixture
def bots(client, ctx, clock):
    def step(seconds):
        for _ in range(max(1, int(seconds * 2))):
            clock.t[0] += seconds / max(1, int(seconds * 2))
            for a in ctx.topology.approaches:
                ctx.store.set_traffic(a, 0, "simulated")
            ctx.incidents.tick(); ctx.ev.tick(); ctx.roadusers.tick()
    lines = []
    return Bots(client, step=step, say=lines.append)


def test_full_run_passes_and_prints_no_tokens(bots, ctx, capsys):
    chk, guests = bots.run_once(vehicles=5, ambulances=1, seed=1)
    table = chk.table()
    failed = [r for r in chk.rows if not r[1]]
    assert chk.ok, f"failed checks: {failed}\n{table}"
    assert "PASS" in table and "FAIL" not in table
    for g in guests:
        assert g.token not in table, "tokens are never printed"
    assert ctx.roadusers.users == {}, "every session is deleted at the end"


def test_run_checks_the_important_rules(bots):
    chk, _ = bots.run_once(vehicles=4, ambulances=1, seed=2)
    names = [r[0] for r in chk.rows]
    for needle in ("a vehicle cannot raise an emergency alert", "alert rate limit", "were warned", "really deleted", "never say hurry"):
        assert any(needle in n for n in names), needle
    assert chk.ok


def test_too_many_bots_for_the_join_limit_fails_clearly(bots):
    chk, _ = bots.run_once(vehicles=10, ambulances=1)
    assert not chk.ok and chk.rows[0][0] == "arguments" and "limit" in chk.rows[0][2]


def test_ambulance_count_is_limited(bots):
    chk, _ = bots.run_once(vehicles=2, ambulances=4)
    assert not chk.ok and "ambulance" in chk.rows[0][2]


def test_a_backend_that_keeps_sessions_makes_the_run_fail(client, ctx, clock):
    """If DELETE did nothing, the 'sessions are really deleted' check must report FAIL."""
    class KeepsSessions:
        def __getattr__(self, name):
            return getattr(client, name)

        def delete(self, *a, **k):
            return client.get("/api/health")

    def step(seconds):
        for _ in range(max(1, int(seconds * 2))):
            clock.t[0] += seconds / max(1, int(seconds * 2))
            for ap in ctx.topology.approaches:
                ctx.store.set_traffic(ap, 0, "simulated")
            ctx.incidents.tick(); ctx.ev.tick(); ctx.roadusers.tick()
    chk, _ = Bots(KeepsSessions(), step=step).run_once(vehicles=2, ambulances=1, seed=3)
    bad = [r[0] for r in chk.rows if not r[1]]
    assert not chk.ok and any("really deleted" in n for n in bad), bad


def test_check_table_marks_failures():
    c = Check()
    c.add("a", True, "fine")
    c.add("b", False, "broken")
    t = c.table()
    assert "PASS  a" in t and "FAIL  b" in t and "SOME CHECKS FAILED" in t and not c.ok


def test_unreachable_backend_exits_with_code_2(capsys):
    assert main(["--base", "http://127.0.0.1:9", "--vehicles", "1"]) == 2
    assert "cannot reach the backend" in capsys.readouterr().out
