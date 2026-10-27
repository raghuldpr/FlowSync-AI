"""Incident detection, impact assessment and the operator alert lifecycle.

Detected types (deterministic rules, thresholds in topology.yaml):
  congestion_spike  occupancy >= threshold continuously for hold_s; auto-clears below the clear threshold.
  stalled_vehicle   an object in the ROI has been stationary >= stall_s WHILE its axis has been green that
                    long (a queue at red is not a stall) and the downstream link is not full (that is
                    spillback, not a stall). A stalled vehicle closes its edge for routing.
Lifecycle: open -> acknowledged -> resolved (by operator or automatically). All operator actions are audited
by the API layer.
"""
import itertools
import json
import threading
from typing import Optional

from ..audit import event
from ..db import now_iso
from .routing import dijkstra, edge_costs

TYPES = ("congestion_spike", "stalled_vehicle")
SEVERITY = {"congestion_spike": "medium", "stalled_vehicle": "high"}
EXT_NODES = ("ext_W", "ext_E", "ext_S")


class IncidentManager:
    def __init__(self, topology, store, db):
        self.topo, self.store, self.db = topology, store, db
        self._lock = threading.RLock()
        self._cong = {}                                        # approach -> {since, clear}
        self.open = {}                                         # (type, approach) -> incident id
        for r in db.query("SELECT id,type,approach FROM incidents WHERE status!='resolved'"):
            self.open[(r["type"], r["approach"])] = r["id"]
            if r["type"] == "stalled_vehicle":
                store.incident_closed.add(r["approach"])

    # ---- lifecycle ----------------------------------------------------------------------------
    def create(self, type_: str, approach: str, source: str, details: dict = None) -> Optional[int]:
        assert type_ in TYPES and approach in self.topo.approaches
        with self._lock:
            if (type_, approach) in self.open:
                return None                                    # one open incident per (type, approach)
            iid = self.db.execute(
                "INSERT INTO incidents(type,approach,status,severity,source,detected_at,details) VALUES(?,?,?,?,?,?,?)",
                (type_, approach, "open", SEVERITY[type_], source, now_iso(), json.dumps(details or {})))
            self.open[(type_, approach)] = iid
            if type_ == "stalled_vehicle":
                self.store.incident_closed.add(approach)
            event(self.db, "incident_opened", source, "warning",
                  {"incident": iid, "type": type_, "approach": approach, "details": details or {}})
            return iid

    def acknowledge(self, iid: int, actor: str) -> bool:
        with self._lock:
            row = self.db.one("SELECT status FROM incidents WHERE id=?", (iid,))
            if row is None or row["status"] != "open":
                return False
            self.db.execute("UPDATE incidents SET status='acknowledged', ack_by=?, ack_at=? WHERE id=?",
                            (actor, now_iso(), iid))
            return True

    def resolve(self, iid: int, actor: str, reason: str) -> bool:
        with self._lock:
            row = self.db.one("SELECT * FROM incidents WHERE id=?", (iid,))
            if row is None or row["status"] == "resolved":
                return False
            self.db.execute("UPDATE incidents SET status='resolved', resolved_by=?, resolved_at=?, resolve_reason=? "
                            "WHERE id=?", (actor, now_iso(), reason, iid))
            self.open.pop((row["type"], row["approach"]), None)
            if row["type"] == "stalled_vehicle":
                self.store.incident_closed.discard(row["approach"])
            event(self.db, "incident_resolved", actor, "info", {"incident": iid, "reason": reason})
            return True

    # ---- detection ----------------------------------------------------------------------------
    def tick(self):
        with self._lock:
            now, c = self.store.clock(), self.topo.incidents
            stale = self.topo.control.stale_after_s
            cap = self.topo.control.link_capacity
            for a, ap in self.topo.approaches.items():
                rec = self.store.fresh_record(a, stale)
                self._congestion(a, rec, now, cap, c)
                self._stall(a, ap, rec, cap, c, stale)

    def _congestion(self, a, rec, now, cap, c):
        st = self._cong.setdefault(a, {"since": None, "clear": None})
        key = ("congestion_spike", a)
        if rec is None:
            st["since"] = st["clear"] = None
            return
        occ = rec["count"] / cap
        if key not in self.open:
            if occ >= c.congestion_occupancy:
                st["since"] = st["since"] if st["since"] is not None else now
                if now - st["since"] >= c.congestion_hold_s:
                    self.create("congestion_spike", a, "detector",
                                {"occupancy": round(occ, 2), "count": rec["count"], "held_s": round(now - st["since"], 1)})
                    st["since"] = None
            else:
                st["since"] = None
        else:
            if occ <= c.congestion_clear_occupancy:
                st["clear"] = st["clear"] if st["clear"] is not None else now
                if now - st["clear"] >= c.congestion_clear_hold_s:
                    self.resolve(self.open[key], "auto", "occupancy_cleared")
                    st["clear"] = None
            else:
                st["clear"] = None

    def _stall(self, a, ap, rec, cap, c, stale):
        key = ("stalled_vehicle", a)
        if rec is None:
            return
        if key in self.open:
            src = self.db.one("SELECT source FROM incidents WHERE id=?", (self.open[key],))["source"]
            if src == "detector" and (rec["count"] == 0 or rec["stationary_s"] < 2.0):
                self.resolve(self.open[key], "auto", "object_moved")
            return
        act = self.store.get_actual(ap.intersection, stale)
        if not act or act["phase"] != "GREEN" or act["axis"] != ap.movement:
            return
        if act["elapsed_ms"] / 1000 < c.stall_s or rec["stationary_s"] < c.stall_s or rec["count"] < 1:
            return
        downs = [self.store.fresh_record(d, stale) for d in ap.downstream]
        if any(d is None or d["count"] / cap >= self.topo.control.spillback_occupancy for d in downs):
            return                                             # downstream full or unknown: not attributable to a stall
        self.create("stalled_vehicle", a, "detector", {"stationary_s": round(rec["stationary_s"], 1),
                                                       "green_elapsed_s": round(act["elapsed_ms"] / 1000, 1)})

    # ---- impact assessment --------------------------------------------------------------------
    def impact(self, type_: str, approach: str) -> dict:
        """Deterministic: compares best routes between every external origin/destination pair with and
        without the incident. Added delay is for a vehicle choosing its best route at this moment."""
        t = self.topo
        queues = self.store.fresh_traffic(t.control.stale_after_s)
        other_closed = self.store.all_closed() - {approach}
        base_q = dict(queues)
        base_q[approach] = 0                                   # what the edge would cost without the incident
        base = edge_costs(t, base_q, other_closed)
        with_inc_closed = other_closed | ({approach} if type_ == "stalled_vehicle" else set())
        inc = edge_costs(t, queues, with_inc_closed)
        pairs, worst = [], 0.0
        unreachable = []
        for o, d in itertools.permutations(EXT_NODES, 2):
            b, n = dijkstra(t, base, o, d), dijkstra(t, inc, o, d)
            if b is None or approach not in b[0]:
                continue                                       # this trip never used the affected edge
            if n is None:
                unreachable.append(f"{o}->{d}")
                pairs.append({"from": o, "to": d, "base_path": b[0], "base_cost_s": round(b[1], 1),
                              "alt_path": None, "alt_cost_s": None, "added_delay_s": None, "rerouted": False})
                continue
            added = max(0.0, n[1] - b[1])
            worst = max(worst, added)
            pairs.append({"from": o, "to": d, "base_path": b[0], "base_cost_s": round(b[1], 1),
                          "alt_path": n[0], "alt_cost_s": round(n[1], 1), "added_delay_s": round(added, 1),
                          "rerouted": n[0] != b[0]})
        feeders = [x.id for x in t.approaches.values() if approach in x.downstream]
        edges = [approach] + feeders
        return {"affected_edges": edges,
                "affected_intersections": sorted({t.approaches[e].intersection for e in edges}),
                "routes_affected": len(pairs), "max_added_delay_s": round(worst, 1),
                "unreachable_pairs": unreachable, "pairs": pairs}

    # ---- reads --------------------------------------------------------------------------------
    def list(self, status: str = None, limit: int = 50) -> list:
        sql, args = "SELECT * FROM incidents", ()
        if status == "active":
            sql += " WHERE status!='resolved'"
        elif status:
            sql, args = sql + " WHERE status=?", (status,)
        rows = self.db.query(sql + " ORDER BY id DESC LIMIT ?", args + (limit,))
        for r in rows:
            r["details"] = json.loads(r["details"]) if r["details"] else {}
            r["impact"] = self.impact(r["type"], r["approach"]) if r["status"] != "resolved" else None
        return rows

    def unacknowledged(self) -> int:
        return self.db.one("SELECT COUNT(*) n FROM incidents WHERE status='open'")["n"]
