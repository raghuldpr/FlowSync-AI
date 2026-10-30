import json

from .db import Database, now_iso


def audit(db: Database, actor: str, role: str, action: str, target: str = None, params=None, result: str = "ok"):
    db.append_audit(now_iso(), actor, role, action, target, json.dumps(params) if params is not None else None, result)


def event(db: Database, type_: str, source: str, severity: str = "info", payload=None):
    db.execute("INSERT INTO system_events(ts,type,source,severity,payload) VALUES(?,?,?,?,?)",
               (now_iso(), type_, source, severity, json.dumps(payload) if payload is not None else None))
