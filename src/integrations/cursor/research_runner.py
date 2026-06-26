# Cursor-backed research helpers — mock fixtures and ResearchOutput mapping.

from __future__ import annotations

from src.core.schemas import AffectedFile, Hypothesis, ResearchReport
from src.core.state import JiraTicket, ResearchOutput

_DEMO_PATH = "src/agents/planner/fixtures/demo/UserSearchController.py"


def mock_research_report(ticket: JiraTicket) -> ResearchReport:
    """Deterministic research for dry-run / tests — no Cursor API call."""
    ticket_id = ticket.get("issue_id", "UNKNOWN")
    return ResearchReport(
        ticket_id=ticket_id,
        research_summary=(
            "Mock Cursor research: SQL injection likely from string concatenation in user search. "
            "cursor_search would target search/SQL patterns; demo fixture path provided for local runs."
        ),
        hypotheses=[
            Hypothesis(
                rank=1,
                hypothesis="User input concatenated into SQL in search handler",
                evidence_strength="moderate",
                evidence=["Ticket describes SQLi in user search endpoint", f"Candidate: {_DEMO_PATH}"],
            )
        ],
        affected_files=[
            AffectedFile(
                path=_DEMO_PATH,
                reason="Named in security ticket / demo fixture for SQLi pattern",
                evidence_strength="moderate",
            )
        ],
        affected_modules=[ticket.get("module") or "auth"],
        module_blast_radius=[],
        architectural_risk="medium",
        issue_type_assessment="code_bug",
        flags=["cursor_research_dry_run"],
    )


def report_to_research_output(report: ResearchReport) -> ResearchOutput:
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
