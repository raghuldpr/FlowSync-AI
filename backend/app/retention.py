"""Data-minimisation job: old operational records are deleted on a schedule (and on demand by an admin).

Kept indefinitely: the audit log (accountability), incidents, hazards, EV sessions and warning history (small, and the
evidence for the demo). Purged: raw telemetry that is large or privacy-relevant.
"""
import time
from datetime import datetime, timedelta, timezone

from . import images

WINDOWS = {                      # table -> (timestamp column, keep for)
    "traffic_snapshots": ("ts", timedelta(hours=24)),
    "ev_positions": ("ts", timedelta(hours=6)),
    "signal_decisions": ("ts", timedelta(days=7)),
    "system_events": ("ts", timedelta(days=7)),
    "device_commands": ("issued_at", timedelta(hours=24)),
}
KEEP_REPLAYS = 5


def run(ctx, now: datetime = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = {}
    for table, (col, keep) in WINDOWS.items():
        cutoff = (now - keep).isoformat(timespec="milliseconds")
        n = ctx.db.one(f"SELECT COUNT(*) n FROM {table} WHERE {col} < ?", (cutoff,))["n"]
        if n:
            ctx.db.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff,))
        out[table] = n
    n = ctx.db.one("SELECT COUNT(*) n FROM sessions WHERE expires_at < ?", (time.time(),))["n"]
    ctx.db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    out["sessions"] = n
    out["images"] = images.purge_old(ctx.db, ctx.settings.image_dir, now.timestamp())
    old = [r["run_id"] for r in ctx.db.query("SELECT run_id FROM replay_runs ORDER BY started_at DESC LIMIT -1 OFFSET ?", (KEEP_REPLAYS,))]
    for rid in old:
        ctx.db.execute("DELETE FROM replay_frames WHERE run_id=?", (rid,))
        ctx.db.execute("DELETE FROM replay_runs WHERE run_id=?", (rid,))
    out["replay_runs"] = len(old)
    ctx.metrics.count("retention_runs")
    return out
