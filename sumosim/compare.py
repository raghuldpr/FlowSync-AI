"""Fixed-timer vs FlowSync on the SUMO triangle.   python -m sumosim.compare [--seeds 5] [--quick]

A  Signals: the same SUMO demand with the controllers on their local fixed-time cycle (backend link down) vs the adaptive backend.
B  Ambulance: the same West -> South trip with a jam on the direct link L13, with signal priority + live rerouting OFF vs ON.
Everything is SIMULATED (SUMO's driver model, our controller). Numbers show behaviour under that model with their
seed-to-seed spread; they are not a prediction for a real road. Results go to docs/RESULTS_SUMO.md.
"""
import argparse
import statistics
import time
from datetime import datetime, timezone

from app.config import ROOT

from .harness import build_world

PATTERNS = {"balanced demand (0.12 veh/s per entrance)": {"W1": 0.12, "E2": 0.12, "S3": 0.12},
            "rush from the West (0.30 / 0.08 / 0.08)": {"W1": 0.30, "E2": 0.08, "S3": 0.08}}


def run_signals(seed: int, adaptive: bool, rates: dict, warm: int, measure: int) -> dict:
    w = build_world(seed, rates)
    if not adaptive:
        w.sim.link_up = False
    for _ in range(warm):
        w.second()
    s0 = dict(w.sim.stats)
    for _ in range(measure):
        w.second()
    assert not w.sim.unsafe_seen, "a conflicting lamp state occurred"
    s1, n = w.sim.stats, w.sim.stats["arrived"] - s0["arrived"]
    live = [v for v in w.sim.conn.vehicle.getIDList() if v.startswith("v")]
    out = {"served": n, "wait_s": (s1["wait_sum"] - s0["wait_sum"]) / n if n else float("nan"),
           "travel_s": (s1["travel_sum"] - s0["travel_sum"]) / n if n else float("nan"),
           "in_network": len(live), "collisions": s1["collisions"]}
    w.close()
    return out


def run_ev(seed: int, smart: bool, warm: int = 30, horizon: int = 300) -> dict:
    w = build_world(seed, {"W1": 0.04, "E2": 0.04, "S3": 0.04})
    w.ctx.ev.priority_enabled, w.ctx.ev.reroute_enabled = smart, smart
    w.sim.apply({"op": "pin", "approach": "L13", "count": 6})
    for _ in range(warm):
        w.second()
    s = w.ctx.ev.declare("EV-1", "ext_W", "ext_S", "evaluation", "evaluator")
    w.sim.apply({"op": "drive_ev", "vehicle_id": "EV-1"})
    t0 = w.now
    for _ in range(horizon):
        w.second()
        if s.status == "ended":
            break
    assert not w.sim.unsafe_seen
    out = {"time_s": w.now - t0, "arrived": s.status == "ended", "wait_s": w.sim.ev_stats["wait_s"],
           "stops": w.sim.ev_stats["stops"], "route": " > ".join(s.route)}
    w.close()
    return out


def ms(xs):
    xs = [x for x in xs if x == x]
    if not xs:
        return "n/a"
    return f"{statistics.mean(xs):.1f} ± {statistics.stdev(xs) if len(xs) > 1 else 0:.1f}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--quick", action="store_true", help="shorter runs (smoke test)")
    a = p.parse_args()
    warm, measure = (30, 90) if a.quick else (60, 240)
    seeds = range(1, a.seeds + 1)
    t0 = time.time()
    lines = ["# Results: SUMO triangle (SIMULATED)", "",
             f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · {a.seeds} seeds · {warm} s warm-up + {measure} s measured per run. "
             "Mean ± standard deviation across seeds. SUMO's driver model plus our real controller; not a prediction for a real road.", "",
             "## A. Adaptive signals vs fixed-time", "",
             "| Demand | Signals | Vehicles served | Mean wait per vehicle (s) | Mean trip time (s) | Vehicles still in network |", "|---|---|---|---|---|---|"]
    for name, rates in PATTERNS.items():
        for label, adaptive in (("fixed-time", False), ("FlowSync", True)):
            r = [run_signals(sd, adaptive, rates, warm, measure) for sd in seeds]
            print(name, label, ms([x["served"] for x in r]), flush=True)
            assert all(x["collisions"] == 0 for x in r), "collisions in SUMO"
            lines.append(f"| {name} | {label} | {ms([x['served'] for x in r])} | {ms([x['wait_s'] for x in r])} | "
                         f"{ms([x['travel_s'] for x in r])} | {ms([x['in_network'] for x in r])} |")
    lines += ["", "## B. Ambulance West → South with a jam on the direct link (L13)", "",
              "| System | Arrived | Trip time (s) | Seconds stopped | Stops | Route |", "|---|---|---|---|---|---|"]
    for label, smart in (("baseline (no priority, no live reroute)", False), ("FlowSync (priority + reroute)", True)):
        r = [run_ev(sd, smart, horizon=120 if a.quick else 300) for sd in seeds]
        print(label, ms([x["time_s"] for x in r]), flush=True)
        routes = sorted({x["route"] for x in r})
        lines.append(f"| {label} | {sum(x['arrived'] for x in r)}/{len(r)} | {ms([x['time_s'] for x in r])} | "
                     f"{ms([x['wait_s'] for x in r])} | {ms([x['stops'] for x in r])} | {'; '.join(routes)} |")
    lines += ["", f"_Run time {time.time() - t0:.0f} s. Reproduce: `python -m sumosim.compare --seeds {a.seeds}`._", ""]
    out = ROOT / "docs" / ("RESULTS_SUMO_quick.md" if a.quick else "RESULTS_SUMO.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
