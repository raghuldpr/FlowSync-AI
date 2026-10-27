"""Timed, self-checking demo scenarios and failure drills.

A scenario is a script of steps. Each step has narration for the audience, optional backend actions (declare the
EV, raise an incident, ask the assistant ...), optional WORLD commands for the simulator, a CUE telling a human what
to do on the physical tabletop, and an EXPECTATION that is checked against the live system a few seconds later
(shown as a tick/cross, so the demo proves its own claims). Everything runs under the identity of the operator who
started it and is audit-logged. Scenarios use the same APIs and the same deterministic safety rules as real traffic.
"""
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from .audit import audit, event
from .db import now_iso


@dataclass
class Expect:
    after: float                 # seconds after the step starts
    desc: str                    # what should be true
    check: Callable              # (ctx, run) -> (bool, detail)


@dataclass
class Step:
    at: float
    title: str
    narration: str
    actions: list = field(default_factory=list)       # [(name, args)]
    cue: str = ""                                      # what a human does on the PHYSICAL table
    expect: Optional[Expect] = None


@dataclass
class Scenario:
    id: str
    title: str
    kind: str                    # 'demo' | 'drill'
    description: str
    duration: float
    steps: list


class Run:
    def __init__(self, scn: Scenario, user: str, t0: float):
        self.scn, self.user, self.t0 = scn, user, t0
        self.status = "running"
        self.started_idx, self.results = set(), {}      # step index -> ...; expectation index -> {ok, detail, t}
        self.log, self.answer, self.answer_mode = [], "", None
        self.ev_sessions, self.closed, self.incidents, self.users = [], [], [], []
        self.marks = {}
        self.summary = None
        self.run_id = uuid.uuid4().hex                      # id of this run's recording (see /api/operator/replays)
        self.last_frame = -1
        self.floor = {}                                  # db high-water marks at start (so checks only see THIS run)


# ---- helpers used by expectations --------------------------------------------------------------------------------
def _since(ctx, table, run):
    return run.floor.get(table, 0)


def _inc(ctx, type_, approach):
    return ctx.db.one("SELECT * FROM incidents WHERE type=? AND approach=? AND status!='resolved'", (type_, approach))


def _session(ctx, run):
    return ctx.ev.sessions.get(run.ev_sessions[-1]) if run.ev_sessions else None


def chk_incident(type_, approach):
    def f(ctx, run):
        i = _inc(ctx, type_, approach)
        return (i is not None, f"incident #{i['id']} {type_} on {approach} ({i['status']})" if i else f"no open {type_} on {approach}")
    return f


def chk_impact(ctx, run):
    i = _inc(ctx, "congestion_spike", "L13")
    if not i:
        return False, "incident missing"
    im = ctx.incidents.impact(i["type"], i["approach"])
    alt = next((p for p in im["pairs"] if p["rerouted"]), None)
    ok = im["routes_affected"] >= 1 and alt is not None
    return ok, f"{im['routes_affected']} route(s) affected, worst +{im['max_added_delay_s']}s" + (f", alternative {' > '.join(alt['alt_path'])}" if alt else "")


def chk_answer(ctx, run):
    return bool(run.answer), f"answered in mode '{run.answer_mode}'" if run.answer else "no answer"


def chk_route_avoids_congestion(ctx, run):
    s = _session(ctx, run)
    ok = s is not None and "L12" in s.route and "L13" not in s.route
    return ok, f"route {' > '.join(s.route)}" if s else "no EV session"


def chk_reroute_on_closure(ctx, run):
    n = ctx.db.one("SELECT COUNT(*) n FROM system_events WHERE type='ev_reroute' AND id>? AND payload LIKE '%closure%'",
                   (_since(ctx, "events", run),))["n"]
    s = _session(ctx, run)
    return n >= 1 and s is not None and "L13" in s.route, f"{n} closure reroute(s); route now {' > '.join(s.route) if s else '-'}"


def chk_warnings_redirected(ctx, run):
    q = ("SELECT 1 FROM ev_warning_events e JOIN ev_warnings w ON w.id=e.warning_id "
         "WHERE w.session_id=? AND w.approach=? AND e.to_state=?")
    sid = run.ev_sessions[-1]
    cancelled = ctx.db.one(q, (sid, "L12", "cancelled_route_change"))
    redirected = ctx.db.one(q, (sid, "L13", "redirected"))
    return bool(cancelled and redirected), f"L12 warning cancelled: {bool(cancelled)}, L13 warning redirected: {bool(redirected)}"


def chk_priority(inter):
    def f(ctx, run):
        n = ctx.db.one("SELECT COUNT(*) n FROM signal_decisions WHERE intersection=? AND reason LIKE 'emergency%' AND id>?",
                       (inter, _since(ctx, "decisions", run)))["n"]
        return n >= 1, f"{n} emergency-priority decision(s) at {inter}"
    return f


def chk_users(ctx, run):
    from .core.ev_warnings import LIVE
    us = [ctx.roadusers.users.get(t) for t in run.users]
    if len(us) < 2 or None in us:
        return False, "virtual road users missing"
    on_corridor = [k for k, a in us[0].alerts.items() if a["kind"] == "ev" and a["state"] in LIVE]
    other_way = [k for k, a in us[1].alerts.items() if a["kind"] == "ev"]
    return bool(on_corridor) and not other_way, (f"{us[0].id} (on the ambulance's corridor): warned={bool(on_corridor)}; "
                                                  f"{us[1].id} (other direction): warned={bool(other_way)}")


def chk_arrived_clean(ctx, run):
    from .core.ev_warnings import LIVE
    s = _session(ctx, run)
    live = ctx.db.one("SELECT COUNT(*) n FROM ev_warnings WHERE session_id=? AND ended_at IS NULL", (run.ev_sessions[-1],))["n"] if s else -1
    unsafe = ctx.db.one("SELECT COUNT(*) n FROM system_events WHERE type='unsafe_lamps_reported' AND id>?", (_since(ctx, "events", run),))["n"]
    stuck = sum(1 for t in run.users if (u := ctx.roadusers.users.get(t)) for a in u.alerts.values() if a["kind"] == "ev" and a["state"] in LIVE)
    ok = s is not None and s.status == "ended" and live == 0 and unsafe == 0 and stuck == 0
    return ok, f"EV ended={s.status == 'ended' if s else None}, live warnings={live}, live road-user alerts={stuck}, unsafe lamp reports={unsafe}"


def chk_all_clear(ctx, run):
    active = ctx.incidents.list("active")
    return not active, "no active incidents" if not active else "still active: " + ", ".join(
        f"#{i['id']} {i['type']} {i['approach']} ({i['source']})" for i in active)


def chk_traffic_stale(ctx, run):
    q = ctx.store.fresh_traffic(ctx.topology.control.stale_after_s)
    return bool(q) and all(v is None for v in q.values()), "all approaches reported as stale (not zero)"


def chk_traffic_fresh(ctx, run):
    q = ctx.store.fresh_traffic(ctx.topology.control.stale_after_s)
    return any(v is not None for v in q.values()), "fresh readings again"


def chk_controller_holds(ctx, run):
    reasons = {v["reason"] for v in ctx.store.last_full.values()}
    return "no_fresh_data_hold" in reasons, f"controller reasons: {sorted(reasons)}"


def chk_ev_stale(ctx, run):
    s = _session(ctx, run)
    live = len(s.warnings) if s else -1
    return bool(s and s.stale and live == 0 and not ctx.ev.priority_approaches()), f"stale={s.stale if s else None}, live warnings={live}, priority withdrawn"


def chk_ev_recovered(ctx, run):
    s = _session(ctx, run)
    return bool(s and not s.stale and s.edge), "location recovered"


def chk_llm_fallback(ctx, run):
    return run.answer_mode == "deterministic_fallback" and bool(run.answer), f"mode={run.answer_mode}"


def chk_devices_stale(ctx, run):
    st = [ctx.store.get_actual(i, ctx.topology.control.stale_after_s) is None for i in ctx.topology.intersections]
    return all(st), "no fresh device report for any intersection; public view shows 'unknown'"


def chk_devices_back(ctx, run):
    st = [ctx.store.get_actual(i, ctx.topology.control.stale_after_s) is not None for i in ctx.topology.intersections]
    return all(st), "device reports flowing again"


# ---- scenario catalogue ----------------------------------------------------------------------------------------------
def _ambulance() -> Scenario:
    S = Step
    return Scenario(
        "ambulance_demo", "Ambulance through congestion", "demo",
        "Congestion builds on the direct link, the operator is alerted and consults the assistant, an ambulance is dispatched, "
        "a breakdown forces a mid-trip reroute, warnings redirect, signals clear the way, and the system recovers.", 162, [
            S(0, "1 · Normal operation", "Signals adapt to measured demand at three intersections. Lamps shown are what the controller REPORTS, not what was commanded.",
              [("world", {"op": "reset"}), ("world", {"op": "rates", "all": 0.0}),
               ("world", {"op": "rates", "approach": "E2", "rate": 0.04}), ("world", {"op": "rates", "approach": "S3", "rate": 0.04})],
              cue="Place 2-3 toy cars on the East and South entrances only (keep the ring links clear). Check the dashboard shows 'in sync'."),
            S(12, "2 · Congestion builds on the direct West→South link", "The queue on L13 fills three quarters of the lane; after a short hold the system raises a congestion incident.",
              [("world", {"op": "pin", "approach": "L13", "count": "jam"})],
              cue="Park a queue of cars on L13 (the link from Intersection 1 to Intersection 3) filling about three quarters of it.",
              expect=Expect(18, "congestion incident opened on L13", chk_incident("congestion_spike", "L13"))),
            S(34, "3 · Operator alerted with impact assessment", "The alert lists affected intersections, routes and added delay, and proposes an alternative route.",
              [], cue="Point at the Alerts badge and the incident card.",
              expect=Expect(2, "impact assessment lists an alternative route", chk_impact)),
            S(42, "4 · Operator asks the assistant", "The LLM calls tools (traffic, incidents, route alternatives) and explains. Without a model, a deterministic summary is shown.",
              [("llm_ask", {"question": "Why is L13 congested, and which route should an ambulance from the West take?"})],
              cue="Type the question into the Assistant panel.",
              expect=Expect(4, "assistant answered (LLM or deterministic fallback)", chk_answer)),
            S(50, "4b · Other road users join the network", "Every phone or virtual car has a private feed. Only the car on the ambulance's corridor will be warned; the car heading the other way gets nothing.",
              [("spawn_users", {"users": [{"origin": "ext_W", "destination": "ext_S"}, {"origin": "ext_E", "destination": "ext_W"}]})],
              cue="Optional: let the audience open /driver.html on their phones and choose an entrance and a destination.",
              expect=Expect(12, "only the car on the ambulance's corridor is warned", chk_users)),
            S(55, "5 · Ambulance dispatched (West → South)", "The router avoids the congested link. Warnings appear only on approaches the ambulance will reach soon.",
              [("declare_ev", {"vehicle_id": "EV-1", "origin": "ext_W", "destination": "ext_S"}), ("world", {"op": "drive_ev", "vehicle_id": "EV-1", "pace": 3.0})],
              cue="Place the connected car (roof marker) at the West entrance and push it along the route the dashboard shows.",
              expect=Expect(4, "route avoids L13 (goes via Intersection 2)", chk_route_avoids_congestion)),
            S(60, "6 · Breakdown on L12: the route changes", "A stalled vehicle closes L12. The ambulance is rerouted; warnings for the old route are cancelled and warnings on the new route are redirected.",
              [("incident", {"type": "stalled_vehicle", "approach": "L12"}), ("world", {"op": "stall", "approach": "L12", "on": True})],
              cue="Put a stalled car on L12 (Intersection 1 → 2).",
              expect=Expect(6, "EV rerouted via L13; L12 warning cancelled, L13 warning redirected",
                            lambda ctx, run: (lambda a, b: (a[0] and b[0], a[1] + "; " + b[1]))(chk_reroute_on_closure(ctx, run), chk_warnings_redirected(ctx, run)))),
            S(75, "7 · Signals clear the way", "Intersections 1 and 3 give the ambulance priority through the same deterministic safety machine: min green, yellow, all-red.",
              [], cue="Point at the signals turning green ahead of the car (never skipping yellow / all-red).",
              expect=Expect(14, "emergency priority issued at I1 and I3",
                            lambda ctx, run: (lambda a, b: (a[0] and b[0], a[1] + "; " + b[1]))(chk_priority("I1")(ctx, run), chk_priority("I3")(ctx, run)))),
            S(112, "8 · Ambulance arrives; warnings expire", "Priority is released and every warning is cancelled or expired. No unsafe lamp state occurred.",
              [], expect=Expect(8, "EV arrived, no live warnings, no unsafe lamps", chk_arrived_clean)),
            S(124, "9 · Recovery", "The breakdown is cleared; the congestion incident closes itself once occupancy drops.",
              [("resolve_incidents", {}), ("world", {"op": "reset"})], cue="Remove the stalled car and release the queue on L13.",
              expect=Expect(30, "queues drain and every incident clears", chk_all_clear)),
        ])


def _drill_camera() -> Scenario:
    S = Step
    return Scenario("drill_camera_loss", "Drill: camera feed lost", "drill",
                    "The overhead camera stops reporting. Stale data must be shown as stale (never as zero) and signals must hold safely.", 40, [
        S(0, "Baseline", "Traffic data is flowing.", [("world", {"op": "reset"}), ("world", {"op": "rates", "all": 0.08})], cue="Camera running."),
        S(8, "Camera feed dies", "No readings arrive. After 5 s they are STALE: the dashboard says so and the controller holds the current phase.",
          [("world", {"op": "camera", "on": False})], cue="Cover the camera lens or stop the stream.",
          expect=Expect(10, "all readings stale, controller holds ('no_fresh_data_hold')",
                        lambda ctx, run: (lambda a, b: (a[0] and b[0], a[1] + "; " + b[1]))(chk_traffic_stale(ctx, run), chk_controller_holds(ctx, run)))),
        S(24, "Camera restored", "Readings resume and adaptive control continues.", [("world", {"op": "camera", "on": True})], cue="Uncover the lens.",
          expect=Expect(6, "fresh readings again", chk_traffic_fresh)),
    ])


def _drill_ev_lost() -> Scenario:
    S = Step
    return Scenario("drill_ev_location_lost", "Drill: ambulance location lost", "drill",
                    "The ambulance's location goes stale. Warnings must be cancelled and priority withdrawn, then recover when seen again.", 55, [
        S(0, "Ambulance driving", "A normal approach with live warnings.", [("world", {"op": "reset"}), ("declare_ev", {"vehicle_id": "EV-1", "origin": "ext_W", "destination": "ext_S"}),
                                                                           ("world", {"op": "drive_ev", "vehicle_id": "EV-1", "pace": 4.0})], cue="Push the car."),
        S(14, "Location data stops", "No position for 4 s: warnings are cancelled as 'stale' and signal priority is withdrawn.",
          [("world", {"op": "drive_ev", "vehicle_id": "EV-1", "pace": 4.0, "on": False})], cue="Cover the car's marker.",
          expect=Expect(8, "EV location stale, warnings cancelled, priority withdrawn", chk_ev_stale)),
        S(28, "Location regained", "The marker is seen again and tracking resumes.", [("world", {"op": "drive_ev", "vehicle_id": "EV-1", "pace": 4.0})],
          cue="Uncover the marker.", expect=Expect(5, "location recovered", chk_ev_recovered)),
    ])


def _drill_llm() -> Scenario:
    S = Step
    return Scenario("drill_llm_down", "Drill: LLM provider unavailable", "drill",
                    "The model is unreachable. The assistant must fall back to deterministic answers; control is unaffected.", 25, [
        S(0, "Assistant normal", "The assistant works.", [("world", {"op": "reset"})]),
        S(4, "Provider goes down", "Questions are answered by deterministic code from the same tools, clearly labelled.",
          [("llm_down", {}), ("llm_ask", {"question": "Summarise the current traffic situation."})],
          expect=Expect(3, "answered by deterministic fallback", chk_llm_fallback)),
        S(16, "Provider restored", "Normal mode returns.", [("llm_up", {})]),
    ])


def _drill_link() -> Scenario:
    S = Step
    return Scenario("drill_controller_link_loss", "Drill: intersection controller offline", "drill",
                    "The intersection controller loses the backend. The firmware must fall back to a safe fixed-time cycle; the dashboard must flag its reports as stale.", 45, [
        S(0, "Baseline", "Controller in sync.", [("world", {"op": "reset"})]),
        S(8, "Wi-Fi to the controller drops", "After 5 s the device runs its local fixed-time fallback (always yellow + all-red). The dashboard flags stale reports; the public view shows 'unknown'.",
          [("world", {"op": "link", "on": False})], cue="Unplug the router / switch off the controller's Wi-Fi.",
          expect=Expect(12, "device reports stale; public view unknown", chk_devices_stale)),
        S(28, "Link restored", "Commands and reports resume.", [("world", {"op": "link", "on": True})], cue="Restore Wi-Fi.",
          expect=Expect(8, "device reports flowing again", chk_devices_back)),
    ])


def chk_impact_for(approach):
    def f(ctx, run):
        i = _inc(ctx, "congestion_spike", approach)
        if not i:
            return False, "incident missing"
        im = ctx.incidents.impact(i["type"], i["approach"])
        alt = next((p for p in im["pairs"] if p["rerouted"]), None)
        return im["routes_affected"] >= 1 and alt is not None, f"{im['routes_affected']} route(s) affected, worst +{im['max_added_delay_s']}s, {len(im['affected_intersections'])} intersection(s)"
    return f


def chk_route_avoids(edge):
    def f(ctx, run):
        s = _session(ctx, run)
        return s is not None and edge not in s.route, f"route has {len(s.route) if s else 0} roads and avoids {edge}" if s and edge not in s.route else f"route still uses {edge}"
    return f


def chk_priority_at_least(n):
    def f(ctx, run):
        rows = ctx.db.query("SELECT DISTINCT intersection FROM signal_decisions WHERE reason LIKE 'emergency%' AND id>?", (_since(ctx, "decisions", run),))
        return len(rows) >= n, f"emergency priority at {len(rows)} intersections (" + ", ".join(sorted(r["intersection"] for r in rows)) + ")"
    return f


def _city(topo) -> Scenario:
    """Ambulance across the simulated city grid. The jam goes on the second road of the ambulance's free-flow route."""
    from .core.routing import dijkstra, edge_costs
    o, d = topo.ev.default_origin, topo.ev.default_destination
    path = dijkstra(topo, edge_costs(topo, {}, set()), o, d)[0]
    jam = path[1]
    S = Step
    return Scenario(
        "city_demo", "Ambulance across the city (simulated grid)", "demo",
        f"A jam builds on {jam}, an ambulance crosses the {len(topo.intersections)}-intersection city from {o.replace('ext_', '')} to {d.replace('ext_', '')}; "
        "the route avoids the jam and the signals clear a corridor ahead of it.", 185, [
            S(0, "1 · Normal operation", "Every intersection adapts to measured demand; the same controller as the tabletop, simulated at city scale.",
              [("world", {"op": "reset"}), ("world", {"op": "rates", "all": 0.05})]),
            S(12, f"2 · Congestion on {jam}", "A queue forms on the ambulance's direct road.", [("world", {"op": "pin", "approach": jam, "count": "jam"})],
              expect=Expect(18, f"congestion incident on {jam}", chk_incident("congestion_spike", jam))),
            S(34, "3 · Impact assessment", "Which roads and intersections are affected, what the delay is, and the alternative.", [],
              expect=Expect(2, "impact assessment lists an alternative route", chk_impact_for(jam))),
            S(48, "4 · Ambulance dispatched", "The route avoids the jam; priority is requested ahead of the ambulance along the whole corridor.",
              [("declare_ev", {"vehicle_id": "EV-1", "origin": o, "destination": d}), ("world", {"op": "drive_ev", "vehicle_id": "EV-1", "pace": 2.0})],
              expect=Expect(4, f"route avoids the jammed road {jam}", chk_route_avoids(jam))),
            S(90, "5 · A green corridor", "Intersections along the route clear the way through the same safety machine (min green, yellow, all-red).", [],
              expect=Expect(20, "emergency priority at 3 or more intersections", chk_priority_at_least(3))),
            S(140, "6 · Ambulance arrives", "Warnings expire, priority is released, no unsafe lamp state occurred.", [],
              expect=Expect(14, "EV arrived, no live warnings, no unsafe lamps", chk_arrived_clean)),
            S(156, "7 · Recovery", "The jam is released and the city drains.", [("world", {"op": "reset"})],
              expect=Expect(25, "every incident clears", chk_all_clear)),
        ])


def catalogue(topo=None) -> dict:
    """Scenarios for the loaded topology: the tabletop scripts need the triangle's roads; the city script needs the grid."""
    if topo is not None and "ext_W1" in topo.nodes:
        return {s.id: s for s in (_city(topo), _drill_camera_generic(), _drill_llm(), _drill_link())}
    return {s.id: s for s in (_ambulance(), _drill_camera(), _drill_ev_lost(), _drill_llm(), _drill_link())}


def _drill_camera_generic() -> Scenario:
    return _drill_camera()


# ---- runner -------------------------------------------------------------------------------------------------------------
class ScenarioRunner:
    def __init__(self, ctx):
        self.ctx, self.scenarios, self.run = ctx, catalogue(ctx.topology), None
        self._lock = threading.RLock()

    # ---- control
    def start(self, scenario_id: str, user: str) -> Run:
        with self._lock:
            if scenario_id not in self.scenarios:
                raise KeyError(scenario_id)
            if self.run and self.run.status == "running":
                raise RuntimeError("a scenario is already running")
            ctx = self.ctx
            run = Run(self.scenarios[scenario_id], user, ctx.store.clock())
            run.floor = {"events": ctx.db.one("SELECT COALESCE(MAX(id),0) m FROM system_events")["m"],
                         "decisions": ctx.db.one("SELECT COALESCE(MAX(id),0) m FROM signal_decisions")["m"]}
            self.run = run
            ctx.db.execute("INSERT INTO replay_runs(run_id,scenario_id,title,started_at) VALUES(?,?,?,?)",
                           (run.run_id, scenario_id, run.scn.title, now_iso()))
            self._log(run, f"scenario '{run.scn.title}' started by {user}")
            event(ctx.db, "scenario_started", f"scenario:{user}", "info", {"id": scenario_id})
            self.tick()
            return run

    def stop(self, user: str):
        with self._lock:
            if self.run and self.run.status == "running":
                self.run.status = "stopped"
                self._log(self.run, f"stopped by {user}")
                self._cleanup(self.run)

    def _record(self, run: Run, t: float):
        """One compact frame per scenario second: enough to redraw the map and narration later (replay = demo insurance)."""
        sec = int(t)
        if sec <= run.last_frame:
            return
        run.last_frame = sec
        ctx, stale = self.ctx, self.ctx.topology.control.stale_after_s
        now = ctx.store.clock()
        cur = max(run.started_idx, default=None)
        step = run.scn.steps[cur] if cur is not None else None
        frame = {
            "traffic": {a: {"count": r["count"], "stale": now - r["recv_t"] > stale} for a, r in ctx.store.traffic.items()},
            "signals": {i: {"reported": ({"lamps": st["lamps"], "phase": st["phase"], "axis": st["axis"]} if (st := ctx.store.get_actual(i, stale)) else None),
                            "reported_stale": ctx.store.get_actual(i, stale) is None, "mismatch": False} for i in ctx.topology.intersections},
            "ev": [{"status": s["status"], "vehicle_id": s["vehicle_id"], "route": s["route"], "edge": s["edge"], "progress": s["progress"],
                    "stale": s["stale"], "warnings": {a: w["state"] for a, w in s["warnings"].items()}} for s in ctx.ev.snapshot() if s["status"] == "active"],
            "incidents": [{"id": i["id"], "type": i["type"], "approach": i["approach"], "severity": i["severity"], "status": i["status"]}
                          for i in ctx.db.query("SELECT id,type,approach,severity,status FROM incidents WHERE status!='resolved'")],
            "hazards": [{"id": h["id"], "kind": h["kind"], "edge": h["edge"], "progress": h["progress"], "status": h["status"]} for h in ctx.hazards.list("active")],
            "closed_edges": sorted(ctx.store.all_closed()),
            "road_users": len(ctx.roadusers.users),
            "step": None if step is None else {"title": step.title, "narration": step.narration, "cue": step.cue},
            # log lines written during the previous second's tick (they come after that tick's snapshot was taken)
            "events": [e["text"] for e in run.log if sec - 1 <= e["t"] < sec or (sec == 0 and e["t"] <= 0)],
        }
        ctx.db.execute("INSERT INTO replay_frames(run_id,t,data) VALUES(?,?,?)", (run.run_id, float(sec), json.dumps(frame, separators=(",", ":"))))
        ctx.db.execute("UPDATE replay_runs SET frames=frames+1 WHERE run_id=?", (run.run_id,))

    def _cleanup(self, run: Run):
        ctx = self.ctx
        ctx.sim_bus.push({"op": "reset"})
        for sid in run.ev_sessions:
            ctx.ev.end(sid, "scenario_ended")
        for tok in run.users:
            ctx.roadusers.leave(tok)
        for e in run.closed:
            ctx.store.closed_edges.discard(e)
        for iid in run.incidents:
            ctx.incidents.resolve(iid, f"scenario:{run.user}", "scenario_cleanup")
        ctx.llm.forced_down = False

    # ---- time
    def tick(self):
        with self._lock:
            run = self.run
            if run is None or run.status != "running":
                return
            ctx, t = self.ctx, ctx_now(self.ctx, run)
            self._record(run, t)
            for i, step in enumerate(run.scn.steps):
                if i not in run.started_idx and t >= step.at:
                    run.started_idx.add(i)
                    self._log(run, f"▶ {step.title}")
                    for name, args in step.actions:
                        try:
                            self._do(run, name, args)
                        except Exception as e:                     # a failed action must never take the demo down
                            self._log(run, f"! action {name} failed: {type(e).__name__}")
                            event(ctx.db, "scenario_action_failed", f"scenario:{run.user}", "error", {"action": name, "error": type(e).__name__})
                if step.expect and i in run.started_idx and i not in run.results and t >= step.at + step.expect.after:
                    try:
                        ok, detail = step.expect.check(ctx, run)
                    except Exception as e:
                        ok, detail = False, f"check error: {type(e).__name__}"
                    run.results[i] = {"ok": bool(ok), "detail": detail, "t": round(t, 1)}
                    self._log(run, ("✓ " if ok else "✗ ") + step.expect.desc + f" — {detail}")
            if "ev_done" not in run.marks and run.ev_sessions:
                s = ctx.ev.sessions.get(run.ev_sessions[-1])
                if s and s.status == "ended":
                    run.marks["ev_done"] = t
            expects = [i for i, s in enumerate(run.scn.steps) if s.expect]
            if t >= run.scn.duration and all(i in run.results for i in expects):
                run.status = "finished"
                self._record(run, t)
                run.summary = self._summary(run)
                self._log(run, f"finished: {sum(r['ok'] for r in run.results.values())}/{len(run.results)} checks passed")
                self._cleanup(run)

    def _summary(self, run: Run) -> dict:
        ctx = self.ctx
        out = {"checks_passed": sum(r["ok"] for r in run.results.values()), "checks_total": len(run.results)}
        if run.ev_sessions:
            sid = run.ev_sessions[-1]
            out["ev_travel_s"] = round(run.marks["ev_done"] - run.marks["ev_declared"], 1) if "ev_done" in run.marks and "ev_declared" in run.marks else None
            out["warnings_by_final_state"] = {r["state"]: r["n"] for r in ctx.db.query(
                "SELECT state, COUNT(*) n FROM ev_warnings WHERE session_id=? GROUP BY state", (sid,))}
            out["reroutes"] = ctx.db.one("SELECT COUNT(*) n FROM system_events WHERE type='ev_reroute' AND id>?", (run.floor["events"],))["n"]
            out["emergency_priority_decisions"] = ctx.db.one(
                "SELECT COUNT(*) n FROM signal_decisions WHERE reason LIKE 'emergency%' AND id>?", (run.floor["decisions"],))["n"]
        if run.users:
            got = [u for t in run.users if (u := ctx.roadusers.users.get(t)) is not None]
            out["road_users_warned"] = sum(1 for u in got if any(a["kind"] == "ev" for a in u.alerts.values()))
            out["road_users_not_warned"] = sum(1 for u in got if not any(a["kind"] == "ev" for a in u.alerts.values()))
        out["incidents_opened"] = ctx.db.one("SELECT COUNT(*) n FROM system_events WHERE type='incident_opened' AND id>?", (run.floor["events"],))["n"]
        return out

    # ---- actions
    def _log(self, run: Run, text: str):
        run.log.append({"t": round(ctx_now(self.ctx, run), 1), "text": text})
        del run.log[:-200]

    def _do(self, run: Run, name: str, args: dict):
        ctx, actor = self.ctx, f"scenario:{run.user}"
        audit(ctx.db, actor, "operator", f"scenario_action:{name}", None, args)
        t = ctx_now(ctx, run)
        if name == "world":
            cmd = dict(args)
            if cmd.get("count") == "jam":                  # 75 % of the lane: raises the congestion incident without spilling back
                cap = ctx.topology.approaches[cmd["approach"]].capacity
                cmd["count"] = -(-cap * 3 // 4)
            ctx.sim_bus.push(cmd)
        elif name == "declare_ev":
            s = ctx.ev.declare(args["vehicle_id"], args["origin"], args["destination"], "scenario", actor)
            run.ev_sessions.append(s.id)
            run.marks["ev_declared"] = t
        elif name == "incident":
            iid = ctx.incidents.create(args["type"], args["approach"], "scenario", {"declared_by": actor})
            if iid:
                run.incidents.append(iid)
        elif name == "spawn_users":
            for spec in args["users"]:
                u = ctx.roadusers.join(spec["origin"], spec["destination"], simulated=True)
                run.users.append(u.token)
        elif name == "resolve_incidents":
            for iid in list(run.incidents):
                ctx.incidents.resolve(iid, actor, "scenario_recovery")
            run.incidents.clear()
        elif name == "close_edge":
            ctx.store.closed_edges.add(args["edge"]); run.closed.append(args["edge"])
        elif name == "open_edge":
            ctx.store.closed_edges.discard(args["edge"])
        elif name == "llm_ask":
            from .llm import orchestrator
            r = orchestrator.ask(ctx, args["question"], {"username": run.user, "role": "operator"})
            run.answer, run.answer_mode = r["answer"], r["mode"]
            self._log(run, f"assistant [{r['mode']}]: " + r["answer"].replace("\n", " | ")[:300])
        elif name == "llm_down":
            ctx.llm.forced_down = True
        elif name == "llm_up":
            ctx.llm.forced_down = False
        else:
            raise ValueError(f"unknown scenario action {name}")

    # ---- views
    def listing(self) -> list:
        return [{"id": s.id, "title": s.title, "kind": s.kind, "description": s.description, "duration": s.duration}
                for s in self.scenarios.values()]

    def status(self) -> dict:
        with self._lock:
            run, ctx = self.run, self.ctx
            world = "simulator" if ctx.sim_bus.connected() else "physical"
            if run is None:
                return {"state": "idle", "world": world, "scenarios": self.listing()}
            t = ctx_now(ctx, run) if run.status == "running" else (run.log[-1]["t"] if run.log else 0)
            steps = []
            for i, s in enumerate(run.scn.steps):
                res = run.results.get(i)
                steps.append({"at": s.at, "title": s.title, "narration": s.narration, "cue": s.cue,
                              "state": "pending" if i not in run.started_idx else "done",
                              "expect": None if not s.expect else {"desc": s.expect.desc, "ok": None if not res else res["ok"],
                                                                   "detail": None if not res else res["detail"]}})
            cur = max((i for i in run.started_idx), default=None)
            return {"state": run.status, "world": world, "id": run.scn.id, "title": run.scn.title, "kind": run.scn.kind,
                    "started_by": run.user, "t": round(t, 1), "duration": run.scn.duration, "steps": steps,
                    "current": None if cur is None else {"title": run.scn.steps[cur].title, "narration": run.scn.steps[cur].narration,
                                                         "cue": run.scn.steps[cur].cue},
                    "log": run.log[-40:], "summary": run.summary, "scenarios": self.listing()}


def ctx_now(ctx, run: Run) -> float:
    return ctx.store.clock() - run.t0
