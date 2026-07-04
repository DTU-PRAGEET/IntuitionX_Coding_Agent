"""Tests for Jira ticket parsing with partial API field sets."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agents.orchestrator.tools.jira import _issue_to_jira_ticket


class _PropertyHolder:
    """Minimal jira-python PropertyHolder stand-in: missing keys raise AttributeError."""

    def __init__(self, raw: dict):
        """Initialize _PropertyHolder with the provided arguments."""
        self._raw = raw

    def __getattr__(self, name: str):
        """Python special method: getattr."""
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._raw:
            raise AttributeError(f"'PropertyHolder' object has no attribute '{name}'")
        return self._raw[name]


def test_issue_to_jira_ticket_parses_github_repo_link():
    """Test Issue To Jira Ticket Parses Github Repo Link for this module."""
    description = (
        "h2. Repository Link:\n\n"
        "[https://github.com/DTU-PRAGEET/Testing-Security-demo#|smart-link]\n\n"
        "Branch: master\n"
    )
    issue = SimpleNamespace(
        key="SCRUM-5",
        raw={
            "fields": {
                "summary": "SQL Injection vulnerability",
                "description": description,
                "status": {"name": "To Do"},
                "issuetype": {"name": "Security"},
                "priority": {"name": "Medium"},
                "labels": ["security"],
                "components": [],
            }
        },
    )
    issue.fields = _PropertyHolder(issue.raw["fields"])
    client = MagicMock()
    client.comments.return_value = []

    ticket = _issue_to_jira_ticket(issue, client)

    assert ticket["repo_links"] == ["DTU-PRAGEET/Testing-Security-demo"]
    assert ticket["ticket_history_summary"]["github_repo"] == "DTU-PRAGEET/Testing-Security-demo"
    assert ticket["ticket_history_summary"]["target_branch"] == "master"
    assert "vulnerability_category" in ticket


def test_issue_to_jira_ticket_without_assignee_in_property_holder():
    """Test Issue To Jira Ticket Without Assignee In Property Holder for this module."""
    issue = SimpleNamespace(
        key="SCRUM-5",
        raw={
            "fields": {
                "summary": "Demo story",
                "description": "Fix the login bug",
                "status": {"name": "To Do"},
                "issuetype": {"name": "Story"},
                "priority": {"name": "Medium"},
                "labels": [],
                "components": [],
            }
        },
    )
    issue.fields = _PropertyHolder(issue.raw["fields"])
    client = MagicMock()
    client.comments.return_value = []

    ticket = _issue_to_jira_ticket(issue, client)

    assert ticket["issue_id"] == "SCRUM-5"
    assert ticket["assignee"] == "not_specified"
    assert ticket["reporter"] == "not_specified"
    assert ticket["title"] == "Demo story"
