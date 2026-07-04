"""Research Agent — Deep Agent runner (internal loop hidden from LangGraph)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from langsmith import traceable

from src.agents.research.memory import InMemoryResearchMemory
from src.agents.research.research_agent import ResearchAgent
from src.agents.research.tools.file_discovery import extract_anchors
from src.core.config import settings
from src.core.logging import get_logger
from src.core.schemas import ResearchReport
from src.core.state import AgentState, ResearchOutput

_log = get_logger("research")


def _report_to_output(report: ResearchReport) -> ResearchOutput:
    """Convert the Research Agent's report into the pipeline's standard output format"""
    return ResearchOutput(
        ticket_id=report.ticket_id,
        research_summary=report.research_summary,
        hypotheses=[h.model_dump() for h in report.hypotheses],
        affected_files=[f.model_dump() for f in report.affected_files],
        affected_modules=report.affected_modules,
        module_blast_radius=report.module_blast_radius,
        architectural_risk=report.architectural_risk,
        issue_type_assessment=report.issue_type_assessment,
        flags=report.flags,
    )


def _negative_finding_report(ticket_id: str, summary: str, error: str, module: str = "unknown") -> ResearchOutput:
    """Whenever the Research Agent crashes, instead of returning nothing, this function creates a valid ResearchOutput."""
    return ResearchOutput(
        ticket_id=ticket_id,
        research_summary=summary,
        hypotheses=[
            {
                "rank": 1,
                "hypothesis": summary,
                "evidence_strength": "weak",
                "evidence": [error] if error else [],
            }
        ],
        affected_files=[],
        affected_modules=[module],
        module_blast_radius=[],
        architectural_risk="low",
        issue_type_assessment="unknown",
        flags=[f"Research error: {error}"] if error else [],
    )


def _latest_gate_feedback(state: AgentState, gate_type: str) -> str | None:
    """Latest Gate Feedback for this module."""
    gates = state.get("human_gates") or []
    for gate in reversed(gates):
        if gate.get("gate_type") == gate_type and gate.get("status") == "REVISE":
            feedback = gate.get("feedback")
            if feedback:
                return feedback
    return None


@traceable(name="Research Agent — Full Analysis")
def research_node(state: AgentState) -> AgentState:
    """LangGraph node — only exposes ResearchReport artifact, not internal tool loop."""
    now = datetime.now(timezone.utc).isoformat()
    repo_path = state.get("repo_path") or settings.default_repo_path
    ticket = state.get("jira_ticket") or {}
    ticket_id = state.get("jira_ticket_id") or ticket.get("issue_id", "UNKNOWN")

    if not repo_path:
        return {
            **state,
            "research_output": _negative_finding_report(
                ticket_id, "No repository path available", "", ticket.get("module", "unknown")
            ),
            "current_phase": "RESEARCH",
            "prompt_version": "research-v2.0-deep",
            "updated_at": now,
        }

    try:
        memory = InMemoryResearchMemory()
        user_prompt = state.get("user_prompt", "") or ""
        gate_feedback = _latest_gate_feedback(state, "RESEARCH")
        if gate_feedback:
            _log.info("rerun", gate="RESEARCH", feedback_preview=gate_feedback[:200])
            user_prompt = (
                f"{user_prompt}\n\n[Gate 1 feedback — revise research report]\n{gate_feedback}"
            ).strip()
        anchors = extract_anchors(ticket, user_prompt)
        memory.record("anchors", {"anchors": anchors})
        report = ResearchAgent(repo_path).run(
            ticket=ticket,
            user_prompt=user_prompt,
            repo_profile=state.get("repo_profile"),
            anchors=anchors,
            memory=memory,
        )

        research_output = _report_to_output(report)
    except Exception as e:
        research_output = _negative_finding_report(
            ticket_id,
            f"Research failed: {e}",
            str(e),
            ticket.get("module", "unknown"),
        )

    return {
        **state,
        "research_output": research_output,
        "current_phase": "RESEARCH",
        "prompt_version": "research-v2.0-deep",
        "updated_at": now,
    }
