"""Map an EvalCase.ticket (TicketInput) onto the runtime JiraTicket TypedDict.

This is what lets the eval harness bypass live Jira: we synthesize the exact
ticket shape the orchestrator/research agents expect, straight from the case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evals.schema.case import EvalCase
    from src.core.state import JiraTicket


def build_jira_ticket(case: "EvalCase") -> "JiraTicket":
    """Build jira ticket."""
    t = case.ticket
    repo_links = list(t.repo_links)
    if not repo_links:
        repo_links = [r.url for r in case.all_repos() if r.url]
    if not repo_links and case.repo.url:
        repo_links = [case.repo.url]

    ticket: dict = {
        "issue_id": case.case_id,
        "title": t.title,
        "issue_type": t.issue_type,
        "status": t.status,
        "priority_raw": t.priority_raw,
        "priority_normalized": t.priority_normalized,
        "module": t.module,
        "problem_statement": t.problem_statement,
        "acceptance_criteria": list(t.acceptance_criteria),
        "assignee": "eval.harness",
        "reporter": "eval.harness",
        "labels": list(t.labels),
        "repo_links": repo_links,
        "dependencies": [],
        "attachments": [],
        "comments": [],
        "ticket_history_summary": {},
        "flags": [],
        "vulnerability_category": t.vulnerability_category or case.vuln_class,
    }
    return ticket  # type: ignore[return-value]
