"""Research Agent — Deep Agent runner (internal loop hidden from LangGraph)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from langsmith import traceable

from src.agents.research.deep_runner import run_research
from src.agents.research.memory import InMemoryResearchMemory
from src.agents.research.tools.file_discovery import extract_anchors
from src.core.config import settings
from src.core.schemas import ResearchReport
from src.core.state import AgentState, ResearchOutput

SKILLS_PROMPT = Path(__file__).parent / "skills/scripts.md"


def _report_to_output(report: ResearchReport) -> ResearchOutput:
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
        anchors = extract_anchors(ticket, state.get("user_prompt", ""))
        memory.record("anchors", {"anchors": anchors})
        report = run_research(
            ticket=ticket,
            repo_path=repo_path,
            user_prompt=state.get("user_prompt", ""),
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
