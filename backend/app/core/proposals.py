"""Signal-adjustment proposals (from the LLM or an operator). A proposal never changes a signal by itself:

  propose -> deterministic validator (accept/reject, with reason) -> pending
          -> operator confirms in the UI -> validator runs AGAIN -> time-limited request to the controller
          -> the controller still applies min green, yellow and all-red; emergency priority outranks it.
"""
from ..db import now_iso

MIN_S, MAX_S, PENDING_TTL_S = 5, 30, 120

EXPLAIN = {
    "unknown_intersection": "That intersection does not exist.",
    "bad_axis": "Axis must be A or B.",
    "bad_duration": f"Duration must be between {MIN_S} and {MAX_S} seconds.",
    "no_fresh_device_state": "The intersection controller has no fresh state report, so the change cannot be validated.",
    "conflicts_with_emergency_priority": "An emergency vehicle currently has priority on the other axis.",
    "no_fresh_traffic_data": "There is no fresh traffic data for that axis.",
    "all_demand_blocked_by_full_downstream": "Every waiting vehicle on that axis would be released into a full downstream link.",
    "pending_proposal_exists": "A proposal for this intersection is already waiting for confirmation.",
    "ok": "Accepted by the safety validator; waiting for operator confirmation.",
}


class ProposalManager:
    def __init__(self, topology, store, db, controller, ev_priority=None):
        self.topo, self.store, self.db, self.ctrl = topology, store, db, controller

    def _validate(self, inter, target, duration_s) -> str:
        t = self.topo
        if inter not in t.intersections:
            return "unknown_intersection"
        if target not in ("A", "B"):
            return "bad_axis"
        if not isinstance(duration_s, int) or not (MIN_S <= duration_s <= MAX_S):
            return "bad_duration"
        if self.store.get_actual(inter, t.control.stale_after_s) is None:
            return "no_fresh_device_state"
        ev_axes = {t.approaches[a].movement for a in self.ctrl.emergency_approaches()
                   if a in t.approaches and t.approaches[a].intersection == inter}
        if ev_axes and ev_axes != {target}:
            return "conflicts_with_emergency_priority"
        queues = self.store.fresh_traffic(t.control.stale_after_s)
        score, _, blocked, _ = self.ctrl.axis_score(inter, target, queues)
        if score is None:
            return "no_fresh_traffic_data"
        if blocked and not score:
            return "all_demand_blocked_by_full_downstream"
        return "ok"

    def _expire(self):
        self.db.execute("UPDATE llm_proposals SET status='expired' WHERE status='pending' AND created_clock < ?",
                        (self.store.clock() - PENDING_TTL_S,))

    def propose(self, inter, target, duration_s, rationale, actor, via) -> dict:
        self._expire()
        reason = self._validate(inter, target, duration_s)
        if reason == "ok" and self.db.one(
                "SELECT 1 FROM llm_proposals WHERE status='pending' AND intersection=?", (inter,)):
            reason = "pending_proposal_exists"
        status = "pending" if reason == "ok" else "rejected"
        pid = self.db.execute(
            "INSERT INTO llm_proposals(created_at,created_clock,created_by,via,intersection,target,duration_s,"
            "rationale,status,validator_reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), self.store.clock(), actor, via, str(inter)[:8], str(target)[:1], int(duration_s) if isinstance(duration_s, int) else 0,
             (rationale or "")[:300], status, reason))
        return {"id": pid, "status": status, "validator_reason": reason, "explanation": EXPLAIN.get(reason, reason)}

    def confirm(self, pid: int, actor: str):
        """Returns (ok, reason). Re-validates against the CURRENT state before acting."""
        self._expire()
        row = self.db.one("SELECT * FROM llm_proposals WHERE id=?", (pid,))
        if row is None or row["status"] != "pending":
            return False, "not_pending"
        reason = self._validate(row["intersection"], row["target"], row["duration_s"])
        if reason != "ok":
            self.db.execute("UPDATE llm_proposals SET status='rejected', validator_reason=?, decided_by=?, decided_at=? "
                            "WHERE id=?", (reason, actor, now_iso(), pid))
            return False, reason
        self.ctrl.operator_requests[row["intersection"]] = {
            "target": row["target"], "until": self.store.clock() + row["duration_s"], "proposal_id": pid}
        self.db.execute("UPDATE llm_proposals SET status='confirmed', decided_by=?, decided_at=? WHERE id=?",
                        (actor, now_iso(), pid))
        return True, "ok"

    def decline(self, pid: int, actor: str) -> bool:
        self._expire()
        row = self.db.one("SELECT status FROM llm_proposals WHERE id=?", (pid,))
        if row is None or row["status"] != "pending":
            return False
        self.db.execute("UPDATE llm_proposals SET status='declined', decided_by=?, decided_at=? WHERE id=?",
                        (actor, now_iso(), pid))
        return True

    def recent(self, limit: int = 10) -> list:
        self._expire()
        rows = self.db.query("SELECT * FROM llm_proposals ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["explanation"] = EXPLAIN.get(r["validator_reason"], r["validator_reason"])
            r.pop("created_clock", None)
        return rows
