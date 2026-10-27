import random

import pytest

from app.config import Settings
from app.core.fsm import ALL_RED, GREEN, YELLOW, SignalFSM
from app.core.topology import Topology
from app.core.validator import check_switch, lamps_safe, validate_command


@pytest.fixture(scope="module")
def topo():
    return Topology.load(Settings().topology_path)


def run(f, until_ms, start=0, step=100):
    for t in range(start + step, until_ms + 1, step):
        f.tick(t)
    return until_ms


def test_boots_all_red_then_green_a(topo):
    f = SignalFSM(topo.timing, 0)
    assert f.lamps() == {"A": "R", "B": "R"}
    run(f, topo.timing.all_red_ms)
    assert f.phase == GREEN and f.lamps() == {"A": "G", "B": "R"}


def test_random_commands_never_conflict_and_always_clear(topo):
    tm, rng = topo.timing, random.Random(7)
    f = SignalFSM(topo.timing, 0)
    allowed = {(GREEN, YELLOW), (YELLOW, ALL_RED), (ALL_RED, GREEN)}
    prev, seq = f.phase, 0
    entered = {f.phase: 0}
    for t in range(100, 600_000, 100):
        if t % 700 == 0:
            seq += 1
            f.receive_command(seq, rng.choice("AB"), rng.choice([600, 1500, 4000, 9000]), t)
        if t % 53_000 == 0:
            f.set_backend_ok(rng.random() < 0.6)
        f.tick(t)
        assert lamps_safe(f.lamps())
        if f.phase != prev:
            assert (prev, f.phase) in allowed
            dur = t - entered[prev]
            if prev == YELLOW:
                assert dur >= tm.yellow_ms
            if prev == ALL_RED:
                assert dur >= tm.all_red_ms
            entered[f.phase] = t
            prev = f.phase


def test_min_green_is_honoured(topo):
    tm = topo.timing
    f = SignalFSM(tm, 0)
    run(f, tm.all_red_ms)                      # green A starts at all_red_ms
    start = f.since
    f.receive_command(1, "B", 10000, start)
    for t in range(start + 100, start + tm.min_green_ms, 100):
        f.tick(t)
        assert f.phase == GREEN, "must not leave green before min green"
    f.tick(start + tm.min_green_ms)
    assert f.phase == YELLOW


def test_stale_seq_and_bad_input_rejected(topo):
    f = SignalFSM(topo.timing, 0)
    assert f.receive_command(5, "A", 2000, 0)[0]
    assert f.receive_command(5, "B", 2000, 0) == (False, "stale_seq")
    assert f.receive_command(4, "B", 2000, 0) == (False, "stale_seq")
    assert f.receive_command(6, "C", 2000, 0) == (False, "bad_target")
    assert f.receive_command(7, "B", 100, 0) == (False, "bad_validity")


def test_expired_command_is_ignored(topo):
    tm = topo.timing
    f = SignalFSM(tm, 0)
    run(f, tm.all_red_ms)
    f.receive_command(1, "B", 600, f.since)    # valid only 600 ms, less than min green
    run(f, f.since + 60_000, start=f.since)
    assert f.phase == GREEN and f.axis == "A", "expired command must not act later"


def test_fallback_cycle_when_backend_lost(topo):
    tm = topo.timing
    f = SignalFSM(tm, 0)
    f.set_backend_ok(False)
    axes = set()
    for t in range(100, 120_000, 100):
        f.tick(t)
        assert lamps_safe(f.lamps())
        if f.phase == GREEN:
            axes.add(f.axis)
    assert axes == {"A", "B"}


def test_validator():
    class T:                                   # minimal topology stand-in
        intersections = ["I1"]
    assert validate_command(T, {"intersection": "I1", "target": "A", "valid_for_ms": 4000}) == (True, "ok")
    assert validate_command(T, {"intersection": "I9", "target": "A", "valid_for_ms": 4000})[0] is False
    assert validate_command(T, {"intersection": "I1", "target": "X", "valid_for_ms": 4000})[0] is False
    assert validate_command(T, {"intersection": "I1", "target": "A", "valid_for_ms": 99999})[0] is False
    assert not lamps_safe({"A": "G", "B": "G"}) and not lamps_safe({"A": "G", "B": "Y"})
    assert lamps_safe({"A": "Y", "B": "R"})
