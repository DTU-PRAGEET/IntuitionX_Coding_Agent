
from __future__ import annotations

from src.core.schemas import AffectedFile, Hypothesis, ResearchReport
from src.core.state import JiraTicket, ResearchOutput

_DRY_RUN_PATH = "src/example/handler.py"


def mock_research_report(ticket: JiraTicket) -> ResearchReport:
    """Deterministic research for dry-run / tests — no Cursor API call."""
    ticket_id = ticket.get("issue_id", "UNKNOWN")
    return ResearchReport(
        ticket_id=ticket_id,
        research_summary=(
            "Mock Cursor research: dry-run report with placeholder affected file. "
            "Real runs use cursor_search against the repository."
        ),
        hypotheses=[
            Hypothesis(
                rank=1,
                hypothesis="Issue likely in handler code path referenced by ticket",
                evidence_strength="moderate",
                evidence=["Dry-run placeholder hypothesis", f"Candidate: {_DRY_RUN_PATH}"],
            )
        ],
        affected_files=[
            AffectedFile(
                path=_DRY_RUN_PATH,
                reason="Dry-run placeholder affected file",
                evidence_strength="moderate",
            )
        ],
        affected_modules=[ticket.get("module") or "unknown"],
        module_blast_radius=[],
        architectural_risk="medium",
        issue_type_assessment="code_bug",
        flags=["cursor_research_dry_run"],
    )
