"""Cross-checks the portable firmware core (C++) against the Python reference/backend.

The C++ code is compiled for the PC with the `ziglang` wheel (requirements-dev.txt). If it is missing these
tests are skipped. They are what lets us trust the ESP32 logic before any hardware exists:
  * FSM  : golden traces from backend/app/core/fsm.py (random commands, expiry, backend loss, 32-bit clock wrap)
  * HMAC : signatures identical to backend/app/security.py::sign (incl. SHA-256 padding edge cases)
  * JSON : parses the REAL /api/device/commands payload; emits bodies the REAL endpoints accept
  * 595  : bit-serial chain model reproduces the intended lamp pattern; unsafe/unknown input never lights two axes
"""
import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.fsm import SignalFSM
from app.core.topology import Topology
from app.config import ROOT, Settings
from app.security import sign
from simulator.tabletop import DeviceClient

FW = ROOT / "firmware" / "intersection"
CORE = FW / "lib" / "flowsync_core"
pytestmark = pytest.mark.skipif(importlib.util.find_spec("ziglang") is None, reason="needs `pip install ziglang`")


@pytest.fixture(scope="session")
def host(tmp_path_factory):
    d = tmp_path_factory.mktemp("fw")
    zig = [sys.executable, "-m", "ziglang", "c++", "-std=c++11", "-O1", "-I", str(CORE)]
    objs = []
    for src in sorted(CORE.glob("*.cpp")):                       # the firmware sources themselves: warnings are errors
        obj = d / (src.stem + ".o")
        r = subprocess.run([*zig, "-Wall", "-Wextra", "-Werror", "-c", str(src), "-o", str(obj)],
                           capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{src.name} must compile warning-free:\n{r.stderr}"
        objs.append(str(obj))
    out = d / "host_tests.exe"                                   # the PC test driver (uses the std library; noisy headers)
    r = subprocess.run([*zig, "-w", str(FW / "test" / "host_tests.cpp"), *objs, "-o", str(out)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    return str(out)


def run(host, mode, stdin):
    r = subprocess.run([host, mode], input=stdin, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture(scope="module")
def timing():
    return Topology.load(Settings().topology_path).timing


def test_timing_header_matches_topology_yaml():
    sys.path.insert(0, str(ROOT / "firmware" / "tools"))
    import gen_timing
    assert (CORE / "timing_defaults.h").read_text(encoding="utf-8") == gen_timing.render(), \
        "run: python firmware/tools/gen_timing.py"


# ---- FSM golden traces ---------------------------------------------------------------------------------
def scenario(seed, timing, minutes=12):
    """Returns (script_lines_for_cpp(offset), expected_lines) from the Python reference."""
    rng = random.Random(seed)
    fsm = SignalFSM(timing, 0)
    events, expected, seq = [], [], 0
    for t in range(100, minutes * 60_000, 100):
        r = rng.random()
        if t % 700 == 0 or r < 0.004:
            seq += rng.choice([1, 1, 1, 2, -1, 0])                 # includes replayed / older seq numbers
            target = rng.choice("ABAB?")
            valid = rng.choice([100, 600, 1500, 4000, 9000, 20000])
            ok, why = fsm.receive_command(seq, target, valid, t)
            events.append(("C", t, seq, target, valid))
            expected.append(f"C {why}")
        if t % 23_000 == 0 or r > 0.9995:
            on = rng.random() < 0.55
            fsm.set_backend_ok(on)
            events.append(("B", t, int(on)))
        fsm.tick(t)
        s = fsm.state(t)
        events.append(("K", t))
        expected.append(f"K {s['phase']} {s['axis']} {s['next_axis']} {s['lamps']['A']}{s['lamps']['B']} {s['elapsed_ms']}")
    return events, expected


def to_script(events, timing, offset):
    tm = timing
    head = [f"T {tm.min_green_ms} {tm.yellow_ms} {tm.all_red_ms} {tm.max_green_ms} {tm.max_red_ms} "
            f"{tm.fallback_green_ms} {tm.command_valid_ms} {tm.backend_lost_ms}", f"N {offset % 2**32}"]
    body = []
    for e in events:
        if e[0] == "C":
            body.append(f"C {(e[1] + offset) % 2**32} {e[2]} {e[3]} {e[4]}")
        elif e[0] == "B":
            body.append(f"B {(e[1] + offset) % 2**32} {e[2]}")
        else:
            body.append(f"K {(e[1] + offset) % 2**32}")
    return "\n".join(head + body) + "\n"


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("offset", [0, 2**32 - 150_000])       # second case crosses the 32-bit millis() wrap
def test_fsm_matches_python_reference_trace(host, timing, seed, offset):
    events, expected = scenario(seed, timing)
    got = run(host, "fsm", to_script(events, timing, offset)).strip().splitlines()
    assert len(got) == len(expected)
    for a, b in zip(got, expected):
        pa, pb = a.split(), b.split()
        if pa[0] == "K":                       # C++: "K <now> PHASE ..."   Python: "K PHASE ..." (absolute time differs by offset)
            assert pa[2:] == pb[1:], (a, b)
        else:
            assert pa == pb, (a, b)
    kinds = {l.split()[1] for l in expected if l[0] == "C"}
    assert {"ok", "stale_seq", "bad_target", "bad_validity"} <= kinds, "scenario must exercise the rejection paths"
    phases = {l.split()[1] for l in expected if l[0] == "K"}
    assert phases == {"GREEN", "YELLOW", "ALL_RED"}


def test_firmware_fsm_never_shows_two_active_axes(host, timing):
    events, _ = scenario(9, timing, minutes=20)
    for l in run(host, "fsm", to_script(events, timing, 0)).splitlines():
        if l.startswith("K"):
            lamps = l.split()[5]
            assert lamps.count("R") >= 1


# ---- signing --------------------------------------------------------------------------------------------
def test_signatures_match_backend_for_sha256_edge_lengths(host):
    rng = random.Random(5)
    lines, expected = [], []
    keys = ["dev-intersection-1-change-me", "k", "K" * 64, "K" * 65, "x" * 200]
    for n in [0, 1, 54, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 1000]:
        body = bytes(rng.randrange(256) for _ in range(n))
        key = rng.choice(keys)
        method, path = rng.choice([("GET", "/api/device/commands"), ("POST", "/api/device/state"), ("POST", "/api/device/command-ack")])
        seq = rng.choice([1, 7, 4294967295])
        lines.append(f"{key} {method} {path} intersection-1 a1b2c3d4 {seq} {body.hex() or '-'}")
        expected.append(sign(key, method, path, "intersection-1", "a1b2c3d4", seq, body))
    assert run(host, "sign", "\n".join(lines) + "\n").split() == expected


# ---- JSON ---------------------------------------------------------------------------------------------
def parse(host, doc):
    out = run(host, "parse", doc).strip().splitlines()
    n = int(out[0].split("=")[1])
    rows = [l.split() for l in out[1:]]
    return n, rows


def test_parses_the_real_commands_endpoint_payload(host, client, ctx, devices):
    for a in ctx.topology.approaches:
        ctx.store.set_traffic(a, 0, "simulated")
    ctx.controller.cycle()
    d = DeviceClient(client, "intersection-1", devices["intersection-1"]["key"])
    r = d.request("GET", "/api/device/commands")
    assert r.status_code == 200
    n, rows = parse(host, r.text)
    want = r.json()["commands"]
    assert n == len(want) == 3
    for row, c in zip(rows, want):
        ok, inter, target, valid, seq, reason = row
        assert (ok, int(inter), target, int(valid), int(seq), reason) == ("1", ctx.topology.intersections.index(c["intersection"]), c["target"], c["valid_for_ms"], c["seq"], "ok")


CMD = {"intersection": "I2", "target": "B", "valid_for_ms": 4000, "reason": "keep_current", "seq": 12}


@pytest.mark.parametrize("doc,n,first", [
    (json.dumps({"commands": [CMD]}), 1, ("1", "1", "B", "4000", "12")),
    (json.dumps({"commands": [CMD]}, indent=4), 1, ("1", "1", "B", "4000", "12")),
    (json.dumps({"extra": {"a": [1, {"b": "}{]["}], "s": 'q"uo\\te}'}, "commands": [dict(reversed(list(CMD.items())))], "tail": None}), 1, ("1", "1", "B", "4000", "12")),
    (json.dumps({"commands": [{**CMD, "reason": 'we"ird } {', "future_field": {"x": [1, 2, 3]}}]}), 1, ("1", "1", "B", "4000", "12")),
    ('{"commands":[]}', 0, None),
    ('  {"commands" : [ ] , "z" : 1 }  ', 0, None),
])
def test_parser_tolerates_formatting_order_and_unknown_fields(host, doc, n, first):
    got_n, rows = parse(host, doc)
    assert got_n == n
    if first:
        assert tuple(rows[0][:5]) == first


@pytest.mark.parametrize("doc", ['{"nope":1}', "", "[]", '{"commands":[{"intersection":"I1"', '{"commands":[1]}', '{"commands":{"a":1}}',
                                 '{"commands":[{"seq":1}', '{"commands":[{"a" 1}]}', '{"commands":[{"a":1} {"b":2}]}'])
def test_malformed_documents_are_rejected_not_crashed(host, doc):
    assert parse(host, doc)[0] == -1


@pytest.mark.parametrize("mut", [{"target": "C"}, {"seq": 1.5}, {"seq": -3}, {"valid_for_ms": "4000"}, {"intersection": "I9"},
                                 {"valid_for_ms": 99999999999}])
def test_invalid_command_fields_are_flagged_for_a_rejected_ack(host, mut):
    n, rows = parse(host, json.dumps({"commands": [{**CMD, **mut}]}))
    assert n == 1 and rows[0][0] == "0" and rows[0][-1] == "bad_command"


def test_missing_required_field_is_flagged(host):
    c = dict(CMD)
    del c["seq"]
    n, rows = parse(host, json.dumps({"commands": [c]}))
    assert n == 1 and rows[0][0] == "0"


def test_more_commands_than_capacity_is_capped(host):
    n, rows = parse(host, json.dumps({"commands": [{**CMD, "seq": i} for i in range(20)]}))
    assert n == 8 and len(rows) == 8


def test_state_and_ack_bodies_are_accepted_by_the_real_backend(host, client, ctx, devices):
    from app.api.device import AckIn, StateIn
    stdin = "GREEN A B 1234 G R 1\nYELLOW B A 1999 R Y 0\nALL_RED A B 0 R R 1\n"
    out = run(host, "statejson", stdin).splitlines()
    body = out[1]
    StateIn.model_validate(json.loads(body))
    assert int(out[0]) == len(body) and out[2] == "-1", "an undersized buffer must be reported, not overflowed"
    d = DeviceClient(client, "intersection-1", devices["intersection-1"]["key"])
    d.seq += 1
    raw = body.encode()
    h = {"X-Device-Id": d.id, "X-Boot": d.boot, "X-Seq": str(d.seq), "Content-Type": "application/json",
         "X-Signature": sign(d.key, "POST", "/api/device/state", d.id, d.boot, d.seq, raw)}
    assert client.post("/api/device/state", content=raw, headers=h).status_code == 200
    assert ctx.store.actual["I2"]["state"]["lamps"] == {"A": "R", "B": "Y"} and ctx.store.actual["I1"]["state"]["elapsed_ms"] == 1234
    acks = run(host, "ackjson", "77 I3 1 ok\n78 I1 0 stale_seq\n").splitlines()
    for line, (seq, acc) in zip(acks, [(77, True), (78, False)]):
        m = AckIn.model_validate(json.loads(line))
        assert m.seq == seq and m.accepted is acc


# ---- 595 ---------------------------------------------------------------------------------------------
BITS = {("A", "R"): 0, ("A", "Y"): 1, ("A", "G"): 2, ("B", "R"): 3, ("B", "Y"): 4, ("B", "G"): 5}


def expected_reg(a, b):
    return (1 << BITS[("A", a)]) | (1 << BITS[("B", b)])


def test_chain_outputs_match_the_wiring_map_for_every_lamp_state(host):
    states = [("R", "R"), ("G", "R"), ("Y", "R"), ("R", "G"), ("R", "Y")]       # all states the FSM can produce
    lines, want = [], []
    rng = random.Random(3)
    for _ in range(60):
        trio = [rng.choice(states) for _ in range(3)]
        lines.append(" ".join(a + b for a, b in trio))
        want.append([expected_reg(a, b) for a, b in trio])
    for line, w, got in zip(lines, want, run(host, "leds", "\n".join(lines) + "\n").splitlines()):
        regs = [int(x, 16) for x in got.split()[:3]]
        assert regs == w, (line, got)
        assert "clocks=24" in got and "latches=1" in got and "safe=1" in got


def test_every_register_lights_exactly_one_lamp_per_axis_even_for_garbage(host):
    got = run(host, "leds", "GG XX RR\n").splitlines()[0]
    regs = [int(x, 16) for x in got.split()[:3]]
    assert "safe=0" in got                                          # the safety predicate flags the conflicting pair
    assert regs[2] == 0x09 and regs[1] == 0x09                      # unknown characters fall back to RED
    for r in regs:
        assert bin(r & 0b000111).count("1") == 1 and bin(r >> 3 & 0b111).count("1") == 1


def test_all_red_register_constant():
    assert (1 << BITS[("A", "R")]) | (1 << BITS[("B", "R")]) == 0x09
