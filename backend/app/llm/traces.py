"""Visible trace of every assistant query: the question, each read-tool call, the proposal and the validator verdict,
and which source produced the answer. Read-only record for the operator; it never feeds back into any decision.

The real LLM provider has not been verified against this system (only a stand-in model has), so a trace says which
kind of source produced it instead of implying a tested provider.
"""
import json

from ..db import now_iso
from . import tools as T
from .client import LLMClient

KEEP = 50
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def source_label(client, mode: str) -> dict:
    """What produced the answer: the deterministic fallback, a stand-in model (tests, local server), or an external provider."""
    if mode != "llm":
        return {"kind": "fallback", "label": "Deterministic fallback (no model used)", "model": None, "host": None}
    host = None
    try:
        host = (client.status() or {}).get("provider_host")
    except Exception:
        pass
    model = getattr(client, "model", None)
    if not isinstance(client, LLMClient) or host in LOCAL_HOSTS:
        return {"kind": "stand_in", "label": "Stand-in or local model", "model": model, "host": host}
    return {"kind": "provider", "label": "External provider (live model)", "model": model, "host": host}


def summarise(raw) -> str:
    """One short line describing a tool result: sizes and key names rather than a cut-off JSON dump."""
    def one(k, v):
        if isinstance(v, dict):
            return f"{k}: {len(v)} entries ({', '.join(list(v)[:4])}{', ...' if len(v) > 4 else ''})"
        if isinstance(v, list):
            return f"{k}: {len(v)} items"
        return f"{k}: {v}"
    if isinstance(raw, dict):
        return T.clean("; ".join(one(k, v) for k, v in list(raw.items())[:6]), 240)
    if isinstance(raw, list):
        return T.clean(f"{len(raw)} items", 240)
    return T.clean(str(raw), 240)


def build(ctx, question: str, caller: dict, result: dict, summaries: list) -> dict:
    proposals = []
    for pid in result.get("proposals") or []:
        row = ctx.db.one("SELECT id, intersection, target, duration_s, status, validator_reason FROM llm_proposals WHERE id=?", (pid,))
        if row:
            proposals.append({"id": row["id"], "intersection": row["intersection"], "axis": row["target"], "duration_s": row["duration_s"],
                              "validator_verdict": row["status"], "validator_reason": row["validator_reason"],
                              "note": "Proposal only: a validator vets it and an operator must confirm it."})
    return {
        "question": T.clean(question, 200),
        "source": source_label(ctx.llm, result.get("mode")),
        "fallback_reason": result.get("fallback_reason"),
        "steps": [{"tool": c["name"], "args": c["args"], "ok": c["ok"], "result": summaries[i] if i < len(summaries) else ""}
                  for i, c in enumerate(result.get("tool_calls") or [])],
        "proposals": proposals,
        "answer_chars": len(result.get("answer") or ""),
    }


def record(ctx, question: str, caller: dict, result: dict, summaries: list):
    """Store the trace; a failure here must never affect the answer."""
    try:
        trace = build(ctx, question, caller, result, summaries)
        tid = ctx.db.execute("INSERT INTO llm_traces(ts,actor,trace) VALUES(?,?,?)", (now_iso(), caller["username"], json.dumps(trace)))
        ctx.db.execute("DELETE FROM llm_traces WHERE id <= ?", (tid - KEEP,))
        return tid
    except Exception:
        return None


def recent(ctx, limit: int = 10) -> list:
    out = []
    for r in ctx.db.query("SELECT id, ts, actor, trace FROM llm_traces ORDER BY id DESC LIMIT ?", (limit,)):
        t = json.loads(r["trace"])
        t.update(id=r["id"], ts=r["ts"], actor=r["actor"])
        out.append(t)
    return out
