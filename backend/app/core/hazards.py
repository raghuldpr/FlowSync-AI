"""Road-condition hazards (pothole, debris, wet surface).

Trust model: only an OPERATOR-verified hazard changes anything. Public reports and camera detections arrive as `reported`
and wait for a human; a verified (`open`) hazard adds an explainable travel-time penalty to the road (H_e: 3 / 8 / 15 s for
low / medium / high severity, summed per road and capped at 30 s) and raises an alert for road users heading into it.
Detection is a heuristic aid ("needs review"), never an authority.
"""
import threading

from ..audit import event
from ..db import now_iso

KINDS = ("pothole", "debris", "wet_surface")
SEVERITIES = ("low", "medium", "high")
PENALTY_S = {"low": 3.0, "medium": 8.0, "high": 15.0}
PENALTY_CAP_S = 30.0
WHAT = {"pothole": "pothole", "debris": "debris on the road", "wet_surface": "wet or slippery surface"}
MAX_PENDING_PER_SOURCE = 25


class HazardManager:
    def __init__(self, topology, store, db):
        self.topo, self.store, self.db = topology, store, db
        self._lock = threading.RLock()
        self._refresh()

    # ---- effects ---------------------------------------------------------------------------------
    def _refresh(self):
        pen = {}
        for r in self.db.query("SELECT edge, severity FROM hazards WHERE status='open'"):
            pen[r["edge"]] = min(PENALTY_CAP_S, pen.get(r["edge"], 0.0) + PENALTY_S[r["severity"]])
        self.store.hazard_penalty = pen

    def road_events(self) -> list:
        return [{"key": f"haz:{r['id']}", "kind": "hazard", "edge": r["edge"], "what": WHAT[r["kind"]]}
                for r in self.db.query("SELECT id, kind, edge FROM hazards WHERE status='open'")]

    # ---- lifecycle -------------------------------------------------------------------------------
    def create(self, kind: str, edge: str, severity: str, source: str, status: str = "open", progress: float = None,
               confidence: float = None, image_id: str = None, actor: str = None) -> int:
        if kind not in KINDS or severity not in SEVERITIES or edge not in self.topo.edges or status not in ("open", "reported"):
            raise ValueError("invalid hazard")
        with self._lock:
            if status == "reported" and self.db.one("SELECT COUNT(*) n FROM hazards WHERE status='reported' AND source=?", (source,))["n"] >= MAX_PENDING_PER_SOURCE:
                raise OverflowError("too many unreviewed reports")
            hid = self.db.execute(
                "INSERT INTO hazards(kind,edge,progress,severity,status,source,confidence,image_id,created_at,decided_by,decided_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (kind, edge, progress, severity, status, source, confidence, image_id, now_iso(),
                 actor if status == "open" else None, now_iso() if status == "open" else None))
            event(self.db, "hazard_reported" if status == "reported" else "hazard_opened", source, "warning",
                  {"hazard": hid, "kind": kind, "edge": edge, "severity": severity})
            self._refresh()
            return hid

    def _move(self, hid: int, frm: tuple, to: str, actor: str, col: str = "decided") -> bool:
        with self._lock:
            row = self.db.one("SELECT status FROM hazards WHERE id=?", (hid,))
            if row is None or row["status"] not in frm:
                return False
            if to == "resolved":
                self.db.execute("UPDATE hazards SET status=?, resolved_at=?, decided_by=COALESCE(decided_by,?) WHERE id=?", (to, now_iso(), actor, hid))
            else:
                self.db.execute("UPDATE hazards SET status=?, decided_by=?, decided_at=? WHERE id=?", (to, actor, now_iso(), hid))
            self._refresh()
            return True

    def verify(self, hid: int, actor: str) -> bool:
        return self._move(hid, ("reported",), "open", actor)

    def reject(self, hid: int, actor: str) -> bool:
        return self._move(hid, ("reported",), "rejected", actor)

    def resolve(self, hid: int, actor: str) -> bool:
        return self._move(hid, ("open",), "resolved", actor)

    # ---- reads -----------------------------------------------------------------------------------
    def list(self, status: str = None, limit: int = 50) -> list:
        if status == "active":
            return self.db.query("SELECT * FROM hazards WHERE status IN ('reported','open') ORDER BY id DESC LIMIT ?", (limit,))
        if status:
            return self.db.query("SELECT * FROM hazards WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return self.db.query("SELECT * FROM hazards ORDER BY id DESC LIMIT ?", (limit,))

    def pending(self) -> int:
        return self.db.one("SELECT COUNT(*) n FROM hazards WHERE status='reported'")["n"]
