import csv
import io

from fastapi import APIRouter, Depends, Query, Response

from .. import health, retention
from ..audit import audit
from ..deps import get_ctx, require_admin, require_viewer

router = APIRouter(tags=["admin"])


@router.get("/api/operator/metrics")
def metrics(_=Depends(require_viewer), ctx=Depends(get_ctx)):
    return ctx.metrics.snapshot()


@router.get("/api/operator/health")
def health_report(_=Depends(require_viewer), ctx=Depends(get_ctx)):
    return health.report(ctx)


@router.get("/api/operator/replays")
def replays(_=Depends(require_viewer), ctx=Depends(get_ctx)):
    return ctx.db.query("SELECT run_id, scenario_id, title, started_at, frames FROM replay_runs ORDER BY started_at DESC LIMIT 20")


@router.get("/api/operator/replays/{run_id}")
def replay(run_id: str, _=Depends(require_viewer), ctx=Depends(get_ctx)):
    import json
    from fastapi import HTTPException
    run = ctx.db.one("SELECT * FROM replay_runs WHERE run_id=?", (run_id,))
    if run is None:
        raise HTTPException(404, "no such recording")
    frames = [{"t": r["t"], **json.loads(r["data"])} for r in
              ctx.db.query("SELECT t, data FROM replay_frames WHERE run_id=? ORDER BY t", (run_id,))]
    return {"run": run, "frames": frames}


@router.get("/api/admin/users")
def users(sess=Depends(require_admin), ctx=Depends(get_ctx)):
    """Accounts and roles only; never hashes."""
    return ctx.db.query("SELECT username, role, created_at FROM users ORDER BY username")


def _safe(v):
    """Neutralise spreadsheet formula injection in exported text."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/api/admin/audit.csv")
def audit_csv(limit: int = Query(5000, ge=1, le=100000), sess=Depends(require_admin), ctx=Depends(get_ctx)):
    rows = ctx.db.query("SELECT id, ts, actor, role, action, target, params, result FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    audit(ctx.db, sess["username"], sess["role"], "audit_export", None, {"rows": len(rows)})
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "ts", "actor", "role", "action", "target", "params", "result"])
    for r in rows:
        w.writerow([_safe(r[k]) for k in ("id", "ts", "actor", "role", "action", "target", "params", "result")])
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit.csv"',
                                                                    "X-Content-Type-Options": "nosniff"})


@router.post("/api/admin/retention/run")
def run_retention(sess=Depends(require_admin), ctx=Depends(get_ctx)):
    res = retention.run(ctx)
    audit(ctx.db, sess["username"], sess["role"], "retention_run", None, res)
    return res
