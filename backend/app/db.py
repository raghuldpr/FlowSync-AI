import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY, role TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, role TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT, params TEXT, result TEXT NOT NULL, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS audit_head (
  k INTEGER PRIMARY KEY CHECK (k = 1), last_id INTEGER NOT NULL, last_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_traces (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, trace TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS traffic_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, source TEXT NOT NULL, device_id TEXT NOT NULL,
  approach TEXT NOT NULL, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS signal_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, intersection TEXT NOT NULL,
  target TEXT NOT NULL, reason TEXT NOT NULL, inputs TEXT);
CREATE TABLE IF NOT EXISTS device_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, seq INTEGER NOT NULL,
  intersection TEXT NOT NULL, target TEXT NOT NULL, reason TEXT NOT NULL, issued_at TEXT NOT NULL,
  ack_status TEXT, ack_reason TEXT, acked_at TEXT);
CREATE TABLE IF NOT EXISTS device_status (
  device_id TEXT PRIMARY KEY, role TEXT NOT NULL, last_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS emergency_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, vehicle_id TEXT NOT NULL,
  approach TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, cleared_at TEXT, cleared_by TEXT);
CREATE TABLE IF NOT EXISTS ev_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL,
  status TEXT NOT NULL, declared_by TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
  ended_at TEXT, end_reason TEXT);
CREATE TABLE IF NOT EXISTS ev_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, ts TEXT NOT NULL, edge TEXT NOT NULL,
  progress REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ev_warnings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, approach TEXT NOT NULL, state TEXT NOT NULL,
  eta_band TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, ended_at TEXT);
CREATE TABLE IF NOT EXISTS ev_warning_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, warning_id INTEGER NOT NULL, ts TEXT NOT NULL,
  from_state TEXT, to_state TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, approach TEXT NOT NULL, status TEXT NOT NULL,
  severity TEXT NOT NULL, source TEXT NOT NULL, detected_at TEXT NOT NULL, details TEXT,
  ack_by TEXT, ack_at TEXT, resolved_by TEXT, resolved_at TEXT, resolve_reason TEXT);
CREATE TABLE IF NOT EXISTS llm_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, created_clock REAL NOT NULL,
  created_by TEXT NOT NULL, via TEXT NOT NULL, intersection TEXT NOT NULL, target TEXT NOT NULL,
  duration_s INTEGER NOT NULL, rationale TEXT, status TEXT NOT NULL, validator_reason TEXT NOT NULL,
  decided_by TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS replay_runs (
  run_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, title TEXT NOT NULL, started_at TEXT NOT NULL, frames INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS replay_frames (
  run_id TEXT NOT NULL, t REAL NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_replay_frames ON replay_frames(run_id, t);
CREATE TABLE IF NOT EXISTS hazards (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, edge TEXT NOT NULL, progress REAL, severity TEXT NOT NULL,
  status TEXT NOT NULL, source TEXT NOT NULL, confidence REAL, image_id TEXT, created_at TEXT NOT NULL,
  decided_by TEXT, decided_at TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, path TEXT NOT NULL, bytes INTEGER NOT NULL, edge TEXT, progress REAL,
  created_at TEXT NOT NULL, detection TEXT);
CREATE TABLE IF NOT EXISTS system_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, type TEXT NOT NULL, source TEXT NOT NULL,
  severity TEXT NOT NULL, payload TEXT);
"""


GENESIS = "0" * 64
_AUDIT_COLS = ("ts", "actor", "role", "action", "target", "params", "result")


def audit_hash(prev_hash: str, row_id: int, row) -> str:
    """SHA-256 over the previous row's hash, the row id and the row's content (canonical JSON, so field boundaries are unambiguous)."""
    body = json.dumps([row_id] + [row[c] for c in _AUDIT_COLS], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((prev_hash + "|" + body).encode("utf-8")).hexdigest()


class Database:
    """Single shared connection, serialised by a lock (SQLite is single-writer anyway)."""

    def __init__(self, path: str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate_audit_chain()

    def _migrate_audit_chain(self):
        """Databases created before the chain existed get the two columns, and their existing rows are chained once
        (so tampering AFTER the upgrade is detectable; what happened before it cannot be proven)."""
        with self._lock:
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(audit_log)").fetchall()}
            for c in ("prev_hash", "hash"):
                if c not in cols:
                    self._conn.execute(f"ALTER TABLE audit_log ADD COLUMN {c} TEXT")
            if self._conn.execute("SELECT 1 FROM audit_head").fetchone() is not None:
                return
            prev, last_id = GENESIS, 0
            for r in self._conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall():
                h = audit_hash(prev, r["id"], r)
                self._conn.execute("UPDATE audit_log SET prev_hash=?, hash=? WHERE id=?", (prev, h, r["id"]))
                prev, last_id = h, r["id"]
            self._conn.execute("INSERT INTO audit_head(k,last_id,last_hash) VALUES(1,?,?)", (last_id, prev))

    def append_audit(self, ts, actor, role, action, target, params, result) -> int:
        """Insert one audit row and chain it to the previous one, atomically."""
        row = dict(zip(_AUDIT_COLS, (ts, actor, role, action, target, params, result)))
        with self._lock:
            head = self._conn.execute("SELECT last_hash FROM audit_head WHERE k=1").fetchone()
            prev = head["last_hash"] if head else GENESIS
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rid = self._conn.execute(
                    "INSERT INTO audit_log(ts,actor,role,action,target,params,result,prev_hash) VALUES(?,?,?,?,?,?,?,?)",
                    (ts, actor, role, action, target, params, result, prev)).lastrowid
                h = audit_hash(prev, rid, row)
                self._conn.execute("UPDATE audit_log SET hash=? WHERE id=?", (h, rid))
                self._conn.execute("INSERT INTO audit_head(k,last_id,last_hash) VALUES(1,?,?) "
                                   "ON CONFLICT(k) DO UPDATE SET last_id=excluded.last_id, last_hash=excluded.last_hash", (rid, h))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return rid

    def verify_audit_chain(self) -> dict:
        """Walk the log in id order. Reports the first row that does not match: edited content, a broken link (a row was
        deleted or inserted), a row with no hash, or a missing tail (checked against the stored head)."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
            head = self._conn.execute("SELECT last_id, last_hash FROM audit_head WHERE k=1").fetchone()
        prev, n = GENESIS, 0
        for r in rows:
            n += 1
            if r["hash"] is None or r["prev_hash"] is None:
                return {"ok": False, "rows": n, "broken_at": r["id"], "reason": "row_without_hash"}
            if r["prev_hash"] != prev:
                return {"ok": False, "rows": n, "broken_at": r["id"], "reason": "broken_link"}
            if audit_hash(prev, r["id"], r) != r["hash"]:
                return {"ok": False, "rows": n, "broken_at": r["id"], "reason": "row_modified"}
            prev = r["hash"]
        if head is not None and (head["last_hash"] != prev or head["last_id"] != (rows[-1]["id"] if rows else 0)):
            return {"ok": False, "rows": n, "broken_at": head["last_id"], "reason": "tail_missing"}
        return {"ok": True, "rows": n, "broken_at": None, "reason": None, "head": prev[:16]}

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).lastrowid

    def query(self, sql: str, params: tuple = ()) -> list:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()):
        rows = self.query(sql, params)
        return rows[0] if rows else None
