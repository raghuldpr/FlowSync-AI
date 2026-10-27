"""Repeatable A/B experiments on the virtual tabletop.   python -m simulator.evaluate [--seeds 8] [--quick]

A  Emergency-vehicle benefit. The same ambulance trip (West -> South) with each part of the system switched off in turn:
     baseline        static free-flow route, no signal priority            (what a driver and fixed signals would do)
     reroute only    live congestion-aware route, no signal priority
     priority only   static route, signal priority (green corridor)
     full system     live route + green corridor
   run with a jam on the direct link and without one.
B  Adaptive vs fixed-time signals. Same arrival patterns; "fixed" = the controllers' local fixed-time fallback cycle.

Everything is SIMULATED (the simulator's physics and its ambulance-driver model). The numbers show what the system does under
that model, with its seed-to-seed spread; they are not a prediction for a real road. Results go to docs/RESULTS.md and
dashboard/results.json.
"""
import argparse
import json
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import simulator  # noqa: F401
from app.config import ROOT, Settings, load_yaml
from app.core.routing import dijkstra, edge_costs
from app.core.topology import Topology
from app.main import create_app
from simulator.tabletop import VirtualTabletop

CALM = {"E2": 0.04, "S3": 0.04}


class World:
    """One isolated backend + virtual tabletop on a simulated clock (no sockets, no wall-clock waiting)."""

    def second(self):
        c = self.ctx
        c.incidents.tick(); c.ev.tick(); c.roadusers.tick(); c.controller.cycle()
        self.sim.step(1000)

    @property
    def now(self):
        return self.sim.now_ms / 1000


def build_world(seed: int, rates: dict, city: bool = False) -> World:
    holder = {}
    clock = lambda: holder["sim"].now_ms / 1000 if "sim" in holder else 0.0
    extra = {"topology_path": str(ROOT / "config" / "topology.grid.yaml"), "devices_path": str(ROOT / "config" / "devices.grid.yaml")} if city else {}
    s = Settings(db_path=":memory:", operator_password="eval", controller_autostart=False,
                 llm_enabled=False, llm_base_url="", llm_model="", llm_api_key="", **extra)
    app = create_app(s, clock)
    w = World.__new__(World)
    w.ctx, w.client = app.state.ctx, TestClient(app)
    topo = Topology.load(s.topology_path)
    w.sim = VirtualTabletop(w.client, topo, load_yaml(s.devices_path)["devices"],
                            rates={a: rates.get(a, 0.0) for a in topo.approaches}, seed=seed)
    holder["sim"] = w.sim
    w.sim.step(1000)
    return w


# ---- experiment A: the emergency vehicle ----------------------------------------------------------------------------
ARMS = [("baseline", False, False), ("reroute only", False, True), ("priority only", True, False), ("full system", True, True)]


def run_ev(seed: int, priority: bool, reroute: bool, scenario: str, warm: int = 25, horizon: int = 240) -> dict:
    w = build_world(seed, CALM)
    w.ctx.ev.priority_enabled, w.ctx.ev.reroute_enabled = priority, reroute
    if scenario == "jam":
        w.sim.apply({"op": "pin", "approach": "L13", "count": 6})
    elif scenario == "stall":                       # a stalled vehicle in the direct link: the system knows (incident closes the road)
        w.sim.apply({"op": "stall", "approach": "L13", "on": True})
        w.ctx.incidents.create("stalled_vehicle", "L13", "evaluation")
    for _ in range(warm):
        w.second()
    s = w.ctx.ev.declare("EV-1", "ext_W", "ext_S", "evaluation", "evaluator")
    w.sim.apply({"op": "drive_ev", "vehicle_id": "EV-1", "pace": 3.0})
    t0 = w.now
    for _ in range(horizon):
        w.second()
        if s.status == "ended":
            break
    assert not w.sim.unsafe_seen, "a conflicting lamp state occurred"
    return {"time_s": w.now - t0, "arrived": s.status == "ended", "wait_s": w.sim.ev_stats["wait_s"], "stops": w.sim.ev_stats["stops"],
            "route": s.route}


# ---- experiment B: adaptive vs fixed-time ------------------------------------------------------------------------------
PATTERNS = {
    "balanced demand": {a: 0.10 for a in ("W1", "L21", "L31", "E2", "L12", "L32", "S3", "L13", "L23")},
    "skewed demand (one heavy entrance)": {"E2": 0.45, "W1": 0.03, "S3": 0.03, "L12": 0.02, "L32": 0.02},
    "three heavy entrances": {"W1": 0.28, "E2": 0.28, "S3": 0.28, "L12": 0.04, "L21": 0.04, "L13": 0.04, "L31": 0.04, "L23": 0.04, "L32": 0.04},
}


def run_signals(seed: int, adaptive: bool, rates: dict, warm: int = 20, measure: int = 150) -> dict:
    w = build_world(seed, rates)
    if not adaptive:
        w.sim.link_up = False                       # the controllers run their local fixed-time cycle (8 s per axis)
    for _ in range(warm):
        w.second()
    d0, q_sum, peak = w.sim.departed, 0.0, 0
    for _ in range(measure):
        w.second()
        tot = sum(w.sim.queues.values())
        q_sum += tot
        peak = max(peak, max(w.sim.queues.values()))
    assert not w.sim.unsafe_seen
    served = w.sim.departed - d0
    mean_q = q_sum / measure
    return {"mean_queue": mean_q, "served": served, "delay_s": mean_q / (served / measure) if served else float("inf"), "peak": peak}


# ---- statistics / reporting ---------------------------------------------------------------------------------------------
# ---- experiment C: the simulated 12-intersection city ---------------------------------------------------------------------
CITY_PATTERNS = {"east-west tide (three heavy west entrances)": "tide", "balanced demand": "balanced"}


def _city_rate(pattern: str, a: str) -> float:
    if pattern == "tide":
        return 0.30 if a in ("inW1", "inW2", "inW3") else 0.05
    return 0.10


def run_city_signals(seed: int, adaptive: bool, pattern: str, warm: int = 20, measure: int = 120) -> dict:
    topo = Topology.load(str(ROOT / "config" / "topology.grid.yaml"))
    w = build_world(seed, {a: _city_rate(pattern, a) for a in topo.approaches}, city=True)
    if not adaptive:
        w.sim.link_up = False
    for _ in range(warm):
        w.second()
    d0, q_sum = w.sim.departed, 0.0
    for _ in range(measure):
        w.second()
        q_sum += sum(w.sim.queues.values())
    assert not w.sim.unsafe_seen
    served, mean_q = w.sim.departed - d0, q_sum / measure
    return {"mean_queue": mean_q, "served": served, "delay_s": mean_q / (served / measure) if served else float("inf")}


def run_city_ev(seed: int, priority: bool, reroute: bool, warm: int = 25, horizon: int = 300) -> dict:
    topo = Topology.load(str(ROOT / "config" / "topology.grid.yaml"))
    w = build_world(seed, {a: 0.05 for a in topo.approaches}, city=True)
    w.ctx.ev.priority_enabled, w.ctx.ev.reroute_enabled = priority, reroute
    o, d = topo.ev.default_origin, topo.ev.default_destination
    jam = dijkstra(topo, edge_costs(topo, {}, set()), o, d)[0][1]
    w.sim.apply({"op": "pin", "approach": jam, "count": 6})
    for _ in range(warm):
        w.second()
    s = w.ctx.ev.declare("EV-1", o, d, "evaluation", "evaluator")
    w.sim.apply({"op": "drive_ev", "vehicle_id": "EV-1", "pace": 2.0})
    t0 = w.now
    for _ in range(horizon):
        w.second()
        if s.status == "ended":
            break
    assert not w.sim.unsafe_seen
    return {"time_s": w.now - t0, "arrived": s.status == "ended", "wait_s": w.sim.ev_stats["wait_s"], "stops": w.sim.ev_stats["stops"]}


def pmap(pool, fn, argsets, timeout=900):
    """Run trials in worker processes; one stuck trial raises instead of hanging the whole evaluation."""
    futs = [pool.submit(fn, *a) for a in argsets]
    return [f.result(timeout=timeout) for f in futs]


def ms(xs):
    return (statistics.mean(xs), statistics.stdev(xs) if len(xs) > 1 else 0.0)


def pct(new, base):
    return 100.0 * (new - base) / base if base else float("nan")


def run_all(seeds: int, quick: bool, log=print, city: bool = False, workers: int = 1) -> dict:
    # one trial per worker process: a worker that runs a second world after the first one hangs (leaked TestClient state)
    with ProcessPoolExecutor(max_workers=max(1, workers), max_tasks_per_child=1) as pool:
        return _run_all(pool, seeds, quick, log, city)


def _run_all(pool, seeds: int, quick: bool, log, city: bool) -> dict:
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seeds": seeds, "quick": quick, "ev": {}, "signals": {}}
    for scenario, key in (("free", "free-flowing roads"), ("jam", "a jam (6 vehicles) on the direct link L13"),
                          ("stall", "a stalled vehicle blocking the direct link L13")):
        rows = {}
        for name, prio, rr in ARMS:
            rs = pmap(pool, run_ev, [(sd, prio, rr, scenario) for sd in range(1, seeds + 1)])
            rows[name] = {"time": ms([r["time_s"] for r in rs]), "wait": ms([r["wait_s"] for r in rs]), "stops": ms([r["stops"] for r in rs]),
                          "arrived": sum(r["arrived"] for r in rs), "n": len(rs), "route": rs[0]["route"], "times": [r["time_s"] for r in rs]}
            log(f"  EV [{key}] {name:14s} {rows[name]['time'][0]:6.1f} ± {rows[name]['time'][1]:4.1f} s  waits {rows[name]['wait'][0]:4.1f} s")
        base = rows["baseline"]["times"]
        for name, r in rows.items():                      # paired by seed: same arrivals for every arm
            diffs = [100 * (b - t) / b for b, t in zip(base, r["times"])]
            r["paired_saving_pct"] = ms(diffs)
        out["ev"][key] = rows
    for pname, rates in (PATTERNS.items() if not quick else list(PATTERNS.items())[:2]):
        rows = {}
        for name, ad in (("adaptive", True), ("fixed-time", False)):
            rs = pmap(pool, run_signals, [(sd, ad, rates) for sd in range(1, seeds + 1)])
            rows[name] = {"mean_queue": ms([r["mean_queue"] for r in rs]), "delay_s": ms([r["delay_s"] for r in rs]), "served": ms([r["served"] for r in rs]),
                          "peak": ms([r["peak"] for r in rs]), "delays": [r["delay_s"] for r in rs]}
            log(f"  signals [{pname}] {name:10s} delay {rows[name]['delay_s'][0]:5.1f} ± {rows[name]['delay_s'][1]:4.1f} s  mean queue {rows[name]['mean_queue'][0]:5.2f}")
        diffs = [100 * (f - a) / f for a, f in zip(rows["adaptive"]["delays"], rows["fixed-time"]["delays"])]
        rows["adaptive"]["paired_delay_reduction_pct"] = ms(diffs)
        out["signals"][pname] = rows
    if city:
        out["city"] = {"ev": {}, "signals": {}}
        rows = {}
        for name, prio, rr in (ARMS[0], ARMS[3]):
            rs = pmap(pool, run_city_ev, [(sd, prio, rr) for sd in range(1, seeds + 1)])
            rows[name] = {"time": ms([r["time_s"] for r in rs]), "wait": ms([r["wait_s"] for r in rs]), "stops": ms([r["stops"] for r in rs]),
                          "arrived": sum(r["arrived"] for r in rs), "n": len(rs), "times": [r["time_s"] for r in rs]}
            log(f"  CITY EV {name:12s} {rows[name]['time'][0]:6.1f} ± {rows[name]['time'][1]:4.1f} s  waits {rows[name]['wait'][0]:4.1f} s")
        rows["full system"]["paired_saving_pct"] = ms([100 * (b - t) / b for b, t in zip(rows["baseline"]["times"], rows["full system"]["times"])])
        out["city"]["ev"] = rows
        for pname, pat in CITY_PATTERNS.items():
            prow = {}
            for name, ad in (("adaptive", True), ("fixed-time", False)):
                rs = pmap(pool, run_city_signals, [(sd, ad, pat) for sd in range(1, seeds + 1)])
                prow[name] = {"mean_queue": ms([r["mean_queue"] for r in rs]), "delay_s": ms([r["delay_s"] for r in rs]),
                              "served": ms([r["served"] for r in rs]), "delays": [r["delay_s"] for r in rs]}
                log(f"  CITY signals [{pname}] {name:10s} delay {prow[name]['delay_s'][0]:5.1f} ± {prow[name]['delay_s'][1]:4.1f} s")
            prow["adaptive"]["paired_delay_reduction_pct"] = ms([100 * (f - a) / f for a, f in zip(prow["adaptive"]["delays"], prow["fixed-time"]["delays"])])
            out["city"]["signals"][pname] = prow
    return out


def render_markdown(r: dict) -> str:
    L = ["# Measured results (simulated)", "",
         f"Generated {r['generated']} · {r['seeds']} random seeds{' (quick run)' if r['quick'] else ''} · produced by `python -m simulator.evaluate`.", "",
         "> **Everything here is simulated.** These are outcomes of this project's traffic model and its ambulance-driver model "
         "(an ambulance slows by `1/(1+0.15×queue)` on queued approaches, creeps at a quarter speed past a stalled vehicle, and waits at the stop line for a green). They show what the "
         "system does under that model and how much it varies across seeds; they are **not** a prediction for a real road, and the "
         "physical table has not been measured yet.", "",
         "## A. Ambulance trip, West → South (travel time in simulated seconds, mean ± sd over seeds)", ""]
    for key, rows in r["ev"].items():
        L += [f"**{key}**", "", "| arm | route | travel time (s) | saving vs baseline | time stopped at red (s) | stops | arrived |", "|---|---|---|---|---|---|---|"]
        for name, x in rows.items():
            sv = "—" if name == "baseline" else f"{x['paired_saving_pct'][0]:.0f} % ± {x['paired_saving_pct'][1]:.0f}"
            L.append(f"| {name} | {' › '.join(x['route'])} | {x['time'][0]:.1f} ± {x['time'][1]:.1f} | {sv} | {x['wait'][0]:.1f} ± {x['wait'][1]:.1f} | {x['stops'][0]:.1f} | {x['arrived']}/{x['n']} |")
        L.append("")
    L += ["## B. Adaptive vs fixed-time signals (all intersections)", "",
          "Delay is the mean time a vehicle spends in an approach queue (Little's law: mean queue ÷ throughput). Fixed-time = the controllers' own fallback cycle.", "",
          "| demand pattern | control | mean delay (s) | mean queue (veh.) | vehicles served | delay change (paired) |", "|---|---|---|---|---|---|"]
    for pname, rows in r["signals"].items():
        for name in ("fixed-time", "adaptive"):
            x = rows[name]
            ch = "—" if name == "fixed-time" else f"{x['paired_delay_reduction_pct'][0]:+.0f} % ± {x['paired_delay_reduction_pct'][1]:.0f} (reduction)"
            L.append(f"| {pname} | {name} | {x['delay_s'][0]:.1f} ± {x['delay_s'][1]:.1f} | {x['mean_queue'][0]:.2f} | {x['served'][0]:.0f} | {ch} |")
    if "city" in r:
        L += ["", "## C. Simulated city (12 intersections, 62 roads)", "",
              "Same controller and state machine as the tabletop, on the generated grid (`python -m app.gridgen`). Ambulance from West 1 to East 3 with a jam on the second road of its free-flow route.", "",
              "| arm | travel time (s) | saving vs baseline | time stopped at red (s) | stops | arrived |", "|---|---|---|---|---|---|"]
        for name, x in r["city"]["ev"].items():
            sv = "—" if name == "baseline" else f"{x['paired_saving_pct'][0]:.0f} % ± {x['paired_saving_pct'][1]:.0f}"
            L.append(f"| {name} | {x['time'][0]:.1f} ± {x['time'][1]:.1f} | {sv} | {x['wait'][0]:.1f} ± {x['wait'][1]:.1f} | {x['stops'][0]:.1f} | {x['arrived']}/{x['n']} |")
        L += ["", "| demand pattern | control | mean delay (s) | mean queue (veh.) | vehicles served | delay change (paired) |", "|---|---|---|---|---|---|"]
        for pname, rows in r["city"]["signals"].items():
            for name in ("fixed-time", "adaptive"):
                x = rows[name]
                ch = "—" if name == "fixed-time" else f"{x['paired_delay_reduction_pct'][0]:+.0f} % ± {x['paired_delay_reduction_pct'][1]:.0f} (reduction)"
                L.append(f"| {pname} | {name} | {x['delay_s'][0]:.1f} ± {x['delay_s'][1]:.1f} | {x['mean_queue'][0]:.2f} | {x['served'][0]:.0f} | {ch} |")
    L += ["", "## How to read this", "",
          "* A saving whose ± is larger than its mean is not evidence of a benefit.",
          "* Each arm sees the same random arrivals for a given seed, so comparisons are paired.",
          "* Reproduce: `python -m simulator.evaluate --seeds 8` (about 10 minutes); `--quick` for a smoke run.", ""]
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=8)
    p.add_argument("--quick", action="store_true", help="fewer scenarios (for smoke tests)")
    p.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 2)), help="worker processes")
    p.add_argument("--city", action="store_true", help="also run the 12-intersection city experiments (adds ~10 minutes)")
    p.add_argument("--out", default=str(ROOT / "docs" / "RESULTS.md"))
    p.add_argument("--json", default=str(ROOT / "dashboard" / "results.json"))
    a = p.parse_args()
    t0 = time.time()
    res = run_all(a.seeds, a.quick, city=a.city, workers=a.workers)
    Path(a.out).write_text(render_markdown(res), encoding="utf-8")
    Path(a.json).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"wrote {a.out} and {a.json} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
