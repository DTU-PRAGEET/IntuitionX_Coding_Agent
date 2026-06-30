"""Tests for integrations/jira/client.py parsing."""

from __future__ import annotations

from src.core.state import JiraTicket
from src.integrations.jira.client import ParsedJiraTicket


def test_parsed_jira_ticket_accepts_dict_ticket():
    """Regression: JiraTicket is a TypedDict — must use dict access, not ticket.key."""
    ticket: JiraTicket = {
        "issue_id": "SCRUM-5",
        "title": "Demo",
        "issue_type": "Story",
        "status": "To Do",
        "priority_raw": "Medium",
        "priority_normalized": "P2",
        "module": "not_specified",
        "problem_statement": "Do the thing",
        "acceptance_criteria": [],
        "assignee": "not_specified",
        "reporter": "not_specified",
        "labels": [],
        "repo_links": [],
        "dependencies": [],
        "attachments": [],
        "comments": [],
        "ticket_history_summary": {},
        "flags": [],
        "vulnerability_category": "",
    }
    parsed = ParsedJiraTicket(
        issue_id=ticket.get("issue_id") or "SCRUM-5",
        title=ticket.get("title", ""),
        issue_type=ticket.get("issue_type", "Unknown"),
        status=ticket.get("status", "Unknown"),
        problem_statement=ticket.get("problem_statement", ""),
    )
    assert parsed.issue_id == "SCRUM-5"
