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
  action TEXT NOT NULL, target TEXT, params TEXT, result TEXT NOT NULL);
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


class Database:
    """Single shared connection, serialised by a lock (SQLite is single-writer anyway)."""

    def __init__(self, path: str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).lastrowid

    def query(self, sql: str, params: tuple = ()) -> list:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()):
        rows = self.query(sql, params)
        return rows[0] if rows else None
