"""Emergency-vehicle sessions: tracking, location TTL, passing detection, rerouting, priority and warnings.

The EV's physical position comes from the overhead camera (ArUco) as (edge, progress); the simulator
posts the same thing. Only declared EVs are tracked, and only edge changes are persisted.
"""
import json
import math
import threading
from dataclasses import dataclass, field
from typing import Optional

from ..audit import event
from ..db import now_iso
from .ev_warnings import LIVE, WarningLog, render
from .routing import dijkstra, edge_costs, route_cost


QUEUE_CLEAR_S = 1.4        # seconds a green needs per queued vehicle (discharge ~0.7 vehicles/s)


@dataclass
class EVSession:
    id: int
    vehicle_id: str
    origin: str
    destination: str
    route: list
    edge: Optional[str] = None
    progress: float = 0.0
    last_seen_t: Optional[float] = None
    stale: bool = False
    route_version: int = 0
    last_reroute_t: float = -math.inf
    passed: set = field(default_factory=set)
    warnings: dict = field(default_factory=dict)       # approach -> {id, state, band}
    status: str = "active"


class EVManager:
    def __init__(self, topology, store, db):
        self.topo, self.store, self.db = topology, store, db
        self.log = WarningLog(db)
        self.sessions = {}
        self.priority_enabled = True        # evaluation switches: turn each benefit off to measure it (see simulator/evaluate.py)
        self.reroute_enabled = True
        self._lock = threading.RLock()

    # ---- helpers ------------------------------------------------------------------------------
    def _costs(self) -> dict:
        return edge_costs(self.topo, self.store.fresh_traffic(self.topo.control.stale_after_s),
                          self.store.all_closed(), self.store.hazard_penalty)

    def _label(self, edge_id: str) -> str:
        src = self.topo.edges[edge_id].src
        return self.topo.node_labels.get(src, src)

    def _live_session_for(self, vehicle_id):
        return next((s for s in self.sessions.values() if s.vehicle_id == vehicle_id and s.status == "active"), None)

    # ---- lifecycle ----------------------------------------------------------------------------
    def declare(self, vehicle_id: str, origin: str, destination: str, source: str, actor: str):
        with self._lock:
            if self._live_session_for(vehicle_id):
                raise ValueError("vehicle already has an active emergency session")
            # With rerouting off (evaluation baseline) the route is the static free-flow one: no live traffic knowledge.
            costs = self._costs() if self.reroute_enabled else edge_costs(self.topo, {}, set())
            res = dijkstra(self.topo, costs, origin, destination)
            if res is None:
                raise ValueError("no route between origin and destination")
            sid = self.db.execute(
                "INSERT INTO ev_sessions(vehicle_id,origin,destination,status,declared_by,source,created_at) "
                "VALUES(?,?,?,?,?,?,?)", (vehicle_id, origin, destination, "active", actor, source, now_iso()))
            s = EVSession(sid, vehicle_id, origin, destination, route=res[0])
            self.sessions[sid] = s
            self._purge_old_positions()
            event(self.db, "ev_declared", actor, "warning",
                  {"session": sid, "vehicle": vehicle_id, "route": res[0], "cost": round(res[1], 2)})
            return s

    def end(self, sid: int, reason: str):
        with self._lock:
            s = self.sessions.get(sid)
            if s is None or s.status != "active":
                return False
            for ap, w in list(s.warnings.items()):
                self._transition(s, ap, "expired", f"ev_session_{reason}")
            s.status = "ended"
            self.db.execute("UPDATE ev_sessions SET status='ended', ended_at=?, end_reason=? WHERE id=?",
                            (now_iso(), reason, sid))
            event(self.db, "ev_ended", "ev", "info", {"session": sid, "reason": reason})
            return True

    def _purge_old_positions(self, keep_hours: int = 6):
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=keep_hours)).isoformat(timespec="milliseconds")
        self.db.execute("DELETE FROM ev_positions WHERE ts < ?", (cutoff,))

    # ---- position -----------------------------------------------------------------------------
    def position(self, vehicle_id: str, edge: str, progress: float) -> bool:
        """Returns False when the vehicle has no active session (position is then discarded)."""
        with self._lock:
            s = self._live_session_for(vehicle_id)
            if s is None:
                return False
            now = self.store.clock()
            if s.stale:
                s.stale = False
                event(self.db, "ev_location_recovered", "ev", "info", {"session": s.id})
            if s.edge != edge:
                if s.edge is not None:
                    s.passed.add(s.edge)
                self.db.execute("INSERT INTO ev_positions(session_id,ts,edge,progress) VALUES(?,?,?,?)",
                                (s.id, now_iso(), edge, progress))
            s.edge, s.progress, s.last_seen_t = edge, progress, now
            if self.topo.edges[edge].dst == s.destination and progress >= 0.9:
                self.end(s.id, "arrived")
            else:
                self._evaluate(s)
            return True

    # ---- evaluation (staleness -> reroute -> warnings) ----------------------------------------
    def tick(self):
        with self._lock:
            for s in list(self.sessions.values()):
                if s.status == "active":
                    self._evaluate(s)

    def _evaluate(self, s: EVSession):
        now = self.store.clock()
        if s.edge is None:
            return                                     # declared, not yet located
        if not s.stale and now - s.last_seen_t > self.topo.ev.location_ttl_s:
            s.stale = True
            event(self.db, "ev_location_stale", "ev", "warning", {"session": s.id})
        if s.stale:
            for ap in list(s.warnings):
                self._transition(s, ap, "cancelled_stale", "ev_location_stale")
            return
        self._maybe_reroute(s, now)
        self._sync_warnings(s)

    def _maybe_reroute(self, s: EVSession, now: float):
        if not self.reroute_enabled:
            return
        node = self.topo.edges[s.edge].dst
        if node == s.destination:
            return
        costs = self._costs()
        deviated = s.edge not in s.route
        rest = [] if deviated else s.route[s.route.index(s.edge) + 1:]
        closed_hit = any(e in self.store.all_closed() for e in rest)
        new = dijkstra(self.topo, costs, node, s.destination)
        if new is None:
            return                                     # nothing better to offer; keep the current route
        path, cost = new
        reason = None
        if deviated:
            reason = "deviation"
        elif closed_hit:
            reason = "closure"
        elif path != rest:
            cur = route_cost(rest, costs, self.topo)
            r = self.topo.routing
            if cost < cur * (1 - r.reroute_threshold) and now - s.last_reroute_t >= r.min_reroute_interval_s:
                reason = "congestion"
        if reason:
            old = list(s.route)
            s.route = [s.edge] + path
            s.route_version += 1
            s.last_reroute_t = now
            event(self.db, "ev_reroute", "routing", "warning",
                  {"session": s.id, "reason": reason, "old": old, "new": s.route, "new_cost": round(cost, 2),
                   "threshold": self.topo.routing.reroute_threshold})

    def _relevant(self, s: EVSession) -> dict:
        """approach -> eta band, for approaches on the remaining route that the EV reaches within the horizon."""
        costs, ev = self._costs(), self.topo.ev
        if s.edge not in s.route:
            return {}
        idx = s.route.index(s.edge)
        t_free = lambda e: costs.get(e, self.topo.edges[e].length / self.topo.edges[e].speed)
        out, eta = {}, 0.0
        for k in range(idx, len(s.route)):
            e = s.route[k]
            if k > idx:
                eta = acc
            if eta > ev.warn_horizon_s:
                break
            if e in self.topo.approaches:
                out[e] = "imminent" if eta <= ev.imminent_s else "soon"
            acc = ((1 - s.progress) * t_free(e)) if k == idx else acc + t_free(e)
        return out

    def _transition(self, s: EVSession, approach: str, to: str, reason: str, band: str = None):
        w = s.warnings[approach]
        self.log.transition(w["id"], w["state"], to, reason, now_iso(), band)
        if to in LIVE:
            w["state"] = to
            if band:
                w["band"] = band
        else:
            del s.warnings[approach]

    def _sync_warnings(self, s: EVSession):
        relevant = self._relevant(s)
        for ap, band in relevant.items():
            w = s.warnings.get(ap)
            if w is None:
                state = "redirected" if s.route_version > 0 else "active"
                text = render("approaching", origin=self._label(ap))
                wid = self.log.create(s.id, ap, state, band, text, now_iso())
                s.warnings[ap] = {"id": wid, "state": state, "band": band}
            elif w["band"] != band:
                self._transition(s, ap, "updated", "eta_band_changed", band)
        for ap in list(s.warnings):
            if ap in relevant:
                continue
            if ap in s.passed:
                self._transition(s, ap, "cancelled_passed", "ev_passed_approach")
            elif ap not in s.route:
                self._transition(s, ap, "cancelled_route_change", "approach_no_longer_on_route")
            else:
                self._transition(s, ap, "expired", "outside_warning_window")

    def eta_table(self, s: EVSession) -> list:
        """Remaining route as [{edge, eta_start, eta_end}] in route-time seconds, from the EV's current position."""
        if s.edge not in s.route:
            return []
        costs, out, t = self._costs(), [], 0.0
        for k in range(s.route.index(s.edge), len(s.route)):
            e = s.route[k]
            dur = costs.get(e, self.topo.edges[e].length / self.topo.edges[e].speed)
            if e == s.edge:
                dur *= (1 - s.progress)
            out.append({"edge": e, "eta_start": t, "eta_end": t + dur})
            t += dur
        return out

    def label(self, edge_id: str) -> str:
        return self._label(edge_id)

    # ---- outputs ------------------------------------------------------------------------------
    def priority_approaches(self) -> set:
        """Approaches where signal priority is requested: the EV's current approach, and the next
        one once it is past halfway. Stale or unlocated EVs request nothing."""
        out = set()
        with self._lock:
            for s in self.sessions.values():
                if s.status != "active" or s.edge is None or s.stale or s.edge not in s.route:
                    continue
                if s.edge in self.topo.approaches:
                    out.add(s.edge)
                for r in self.eta_table(s):                       # green corridor: look ahead along the route
                    if r["edge"] in self.topo.approaches and r["eta_start"] <= self.lead_time(r["edge"]):
                        out.add(r["edge"])
        return out if self.priority_enabled else set()

    def lead_time(self, approach: str) -> float:
        """How many route-seconds BEFORE the EV arrives an approach must be asked to serve it.

        The intersection may be mid-green (it must finish minimum green), then needs yellow + all-red, and any queue
        in front of the EV must discharge first. Asking early enough is what turns a stop at red into a clear road.
        """
        tm = self.topo.timing
        q = self.store.fresh_traffic(self.topo.control.stale_after_s).get(approach) or 0
        return (tm.min_green_ms + tm.yellow_ms + tm.all_red_ms) / 1000.0 + 1.0 + QUEUE_CLEAR_S * q

    def public_warning(self, approach: str) -> Optional[dict]:
        """Redacted: text + coarse ETA band only. No vehicle id, route, position or timing."""
        with self._lock:
            best = None
            for s in self.sessions.values():
                w = s.warnings.get(approach) if s.status == "active" else None
                if w and (best is None or (w["band"] == "imminent")):
                    best = w
            if best is None:
                return None
            return {"text": render("approaching", origin=self._label(approach)), "eta_band": best["band"]}

    def snapshot(self) -> list:
        with self._lock:
            return [{"id": s.id, "vehicle_id": s.vehicle_id, "origin": s.origin, "destination": s.destination,
                     "status": s.status, "edge": s.edge, "progress": s.progress, "stale": s.stale,
                     "route": s.route, "route_version": s.route_version,
                     "warnings": {a: dict(w) for a, w in s.warnings.items()}}
                    for s in self.sessions.values()]
