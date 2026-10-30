from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..audit import audit
from ..deps import require_viewer, get_ctx, require_operator
from ..llm import orchestrator, traces

KEEP = traces.KEEP
router = APIRouter(tags=["llm"])


class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@router.get("/api/llm/status")
def llm_status(_=Depends(require_viewer), ctx=Depends(get_ctx)):
    return ctx.llm.status()


@router.post("/api/llm/query")
def llm_query(body: QueryIn, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    """Operator-only. Runs the LLM tool loop, or the deterministic fallback if the model is unavailable."""
    return orchestrator.ask(ctx, body.question, sess)


@router.get("/api/operator/llm/traces")
def llm_traces(limit: int = Query(10, ge=1, le=KEEP), _=Depends(require_operator), ctx=Depends(get_ctx)):
    """Operator-only trace of recent assistant queries: question, tool calls, proposal and validator verdict, answer source."""
    return traces.recent(ctx, limit)


@router.get("/api/operator/proposals")
def proposals(limit: int = Query(10, ge=1, le=50), _=Depends(require_viewer), ctx=Depends(get_ctx)):
    return ctx.proposals.recent(limit)


@router.post("/api/operator/proposals/{pid}/confirm")
def confirm(pid: int, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    ok, reason = ctx.proposals.confirm(pid, sess["username"])
    audit(ctx.db, sess["username"], sess["role"], "proposal_confirm", str(pid), result="ok" if ok else f"rejected_{reason}")
    if not ok:
        raise HTTPException(409, reason)
    return {"ok": True}


@router.post("/api/operator/proposals/{pid}/decline")
def decline(pid: int, sess=Depends(require_operator), ctx=Depends(get_ctx)):
    ok = ctx.proposals.decline(pid, sess["username"])
    audit(ctx.db, sess["username"], sess["role"], "proposal_decline", str(pid), result="ok" if ok else "not_pending")
    if not ok:
        raise HTTPException(409, "not_pending")
    return {"ok": True}
