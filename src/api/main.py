"""FastAPI app — CI/CD error ingestion, approval resume, health checks."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from langgraph.types import Command
from pydantic import BaseModel

from src.core.graph import build_graph
from src.core.schemas import ApprovalDecision
from src.core.state import initial_state
from src.memory.cache import save_failed_attempt

app = FastAPI(title="Sprinklr Jira Solver API")


class CICDErrorPayload(BaseModel):
    """CI/CD failure webhook body — ticket_id, error_log, optional plan_used snapshot.
    Handled by `POST /webhook/cicd-error` to seed self-healing AgentState retry."""
    ticket_id: str
    error_log: str
    session_id: str | None = None
    plan_used: dict | None = None
    stage_failed: str = "Build"


@app.get("/health")
def health():
    """Health for this module."""
    return {"status": "ok"}


@app.post("/approve/{thread_id}")
async def approve_workflow(thread_id: str, decision: ApprovalDecision):
    """Resume a paused workflow (HITL gate or diff confirm) via API."""
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    resume = {"status": decision.status, "feedback": decision.feedback}
    try:
        result = graph.invoke(Command(resume=resume), config=config)
        return {
            "status": "resumed",
            "current_phase": result.get("current_phase"),
            "routing_route": result.get("routing_route"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/webhook/cicd-error")
async def cicd_error_webhook(payload: CICDErrorPayload):
    """Manual CI/CD error upload — triggers self-healing cycle."""
    now = datetime.now(timezone.utc).isoformat()

    failed_attempt = {
        "attempt_id": str(uuid.uuid4()),
        "ticket_id": payload.ticket_id,
        "phase_failed": "CICD",
        "error_type": payload.stage_failed.lower(),
        "error_summary": payload.error_log[:2000],
        "plan_hash": None,
        "plan_used": payload.plan_used,
        "timestamp": now,
    }
    save_failed_attempt(failed_attempt)

    state = initial_state(jira_ticket_id=payload.ticket_id)
    state["current_error_log"] = payload.error_log
    state["retry_count"] = state.get("retry_count", 0) + 1
    state["self_healing_cycle_index"] = state.get("self_healing_cycle_index", 0) + 1

    if state["retry_count"] > state["max_retries"]:
        return {"status": "MAX_RETRIES_EXCEEDED", "action": "manual_intervention_required"}

    graph = build_graph()
    thread_id = payload.session_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph.invoke(state, config=config)
    except Exception as e:
        return {"status": "PAUSED_OR_ERROR", "message": str(e), "thread_id": thread_id}

    return {
        "status": "SELF_HEALING_INITIATED",
        "ticket_id": payload.ticket_id,
        "thread_id": thread_id,
    }
