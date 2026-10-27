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
