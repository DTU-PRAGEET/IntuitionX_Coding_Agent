"""Token-minimal planner context — only the slices the planner needs to plan.

The full AgentState (complete Jira ticket, every previous execution plan with all
old_string/new_string patches, full self-healing notes, human-gate metadata, etc.)
never reaches the planner LLM. These helpers project the state down to:

  * a compact ticket    (id, title, problem statement, acceptance criteria, priority, labels)
  * a research handoff  (summary, hypotheses, affected files/modules, blast radius, flags)
  * a retry summary     (counts + last failure only — NOT every historical plan)
"""

from __future__ import annotations

from typing import Any

from src.core.schemas import ResearchReport
from src.core.state import JiraTicket

_PROBLEM_MAX = 600
_ACCEPTANCE_ITEM_MAX = 200
_ACCEPTANCE_MAX_ITEMS = 6
_EVIDENCE_PER_HYPOTHESIS = 3
_ERROR_SUMMARY_MAX = 300
_RETRY_NOTE_MAX = 120
_LAST_FILES_MAX = 10


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _compact_acceptance(ac: list[str] | str) -> list[str]:
    if isinstance(ac, str):
        if not ac or ac == "not_specified":
            return []
        return [_truncate(ac, _ACCEPTANCE_ITEM_MAX)]
    return [_truncate(item, _ACCEPTANCE_ITEM_MAX) for item in ac[:_ACCEPTANCE_MAX_ITEMS] if item]


def compact_ticket_for_planner(ticket: JiraTicket | None) -> dict[str, Any] | None:
    """Project a Jira ticket to the fields the planner actually plans against.

    Drops reporter, assignee, attachments, comments, dependencies, ticket history,
    and full security enrichment — none of which influence a fresh execution plan.
    """
    if not ticket:
        return None
    return {
        "ticket_id": ticket.get("issue_id", ""),
        "title": _truncate(ticket.get("title") or "", 200),
        "problem_statement": _truncate(ticket.get("problem_statement") or "", _PROBLEM_MAX),
        "acceptance_criteria": _compact_acceptance(ticket.get("acceptance_criteria") or []),
        "priority": ticket.get("priority_normalized") or ticket.get("priority_raw", ""),
        "labels": (ticket.get("labels") or [])[:12],
        "vulnerability_category": ticket.get("vulnerability_category") or "",
    }


def research_handoff_for_planner(report: ResearchReport) -> dict[str, Any]:
    """Research slice the planner needs: summary, hypotheses, files, modules, blast radius.

    Keeps hypotheses (with bounded evidence) since the planner reasons from them, but
    drops issue_type_assessment and any other research-internal bookkeeping.
    """
    return {
        "ticket_id": report.ticket_id,
        "research_summary": report.research_summary,
        "hypotheses": [
            {
                "rank": h.rank,
                "hypothesis": h.hypothesis,
                "evidence_strength": h.evidence_strength,
                "evidence": h.evidence[:_EVIDENCE_PER_HYPOTHESIS],
            }
            for h in report.hypotheses
        ],
        "affected_files": [
            {
                "path": f.path,
                "reason": _truncate(f.reason, 200),
                "evidence_strength": f.evidence_strength,
            }
            for f in report.affected_files
        ],
        "affected_modules": report.affected_modules,
        "module_blast_radius": report.module_blast_radius,
        "architectural_risk": report.architectural_risk,
        "flags": report.flags,
    }


def retry_summary_for_planner(
    previous_attempts: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Collapse the full history of failed attempts into a single retry summary.

    Instead of replaying every previous ExecutionPlan (all steps, old_string/new_string,
    self-healing notes), the planner receives only: how many attempts happened, what the
    last failure was, and which files the last attempt touched.
    """
    attempts = previous_attempts or []
    if not attempts:
        return None
    last = attempts[-1]
    plan_used = last.get("plan_used") or {}
    last_files = list(plan_used.get("files_to_change") or [])[:_LAST_FILES_MAX]
    error_summary = _truncate(last.get("error_summary") or "", _ERROR_SUMMARY_MAX)
    phase = last.get("phase_failed") or "unknown"
    return {
        "num_previous_attempts": len(attempts),
        "last_failed_phase": phase,
        "last_error_type": last.get("error_type") or "unknown",
        "last_error_summary": error_summary,
        "last_files_attempted": last_files,
        "retry_note": f"Previous attempt failed during {phase}: {_truncate(error_summary, _RETRY_NOTE_MAX)}",
    }
