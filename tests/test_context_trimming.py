"""Token-minimal context handoffs — planner and research only get what they need."""

from __future__ import annotations

from src.agents.planner.agent import build_planner_prompt, load_research_report
from src.agents.research.deep_runner import (
    _compact_repo_profile,
    _compact_security_enrichment,
    build_research_user_message,
)
from src.agents.research.memory import InMemoryResearchMemory
from src.core.planner_context import (
    compact_ticket_for_planner,
    research_handoff_for_planner,
    retry_summary_for_planner,
)
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPORT = REPO_ROOT / "src/agents/planner/fixtures/mock_research_report.json"


def _ticket() -> dict:
    return {
        "issue_id": "PLAT-1234",
        "title": "SQL injection in ExampleHandler",
        "issue_type": "Security",
        "status": "In Progress",
        "priority_raw": "High",
        "priority_normalized": "P1",
        "module": "auth",
        "problem_statement": "SQLi via string concat. " + ("detail " * 400),
        "acceptance_criteria": ["Parameterize SQL", "Validate input length"],
        "assignee": "security.team",
        "reporter": "pen.test",
        "labels": ["security", "sql-injection"],
        "repo_links": ["platform/auth-service"],
        "dependencies": [],
        "attachments": [{"name": "scan.pdf", "type": "application/pdf"}],
        "comments": [{"author": "pen.test", "body": "exploit POC here", "created": "2026-01-01"}],
        "ticket_history_summary": {},
        "flags": [],
        "vulnerability_category": "Injection",
    }


def _previous_attempt_with_full_plan() -> dict:
    return {
        "attempt_id": "a1",
        "ticket_id": "PLAT-1234",
        "phase_failed": "IMPLEMENTATION",
        "error_type": "execution_error",
        "error_summary": "old_string mismatch on edit step",
        "plan_hash": "deadbeef",
        "timestamp": "2026-01-01T00:00:00Z",
        "plan_used": {
            "ticket_id": "PLAT-1234",
            "root_hypothesis": "SQLi",
            "files_to_change": ["dao/user.py", "dao/other.py"],
            "self_healing_notes": ["NOTE_SENTINEL_should_not_leak"],
            "execution_steps": [
                {
                    "step_id": 2,
                    "action": "EDIT",
                    "file": "dao/user.py",
                    "target": "fix",
                    "reason": "security",
                    "old_string": "OLD_STRING_SENTINEL_should_not_leak",
                    "new_string": "NEW_STRING_SENTINEL_should_not_leak",
                }
            ],
        },
    }


# ── Planner ──────────────────────────────────────────────────────────────────


def test_compact_ticket_drops_noise_keeps_essentials():
    compact = compact_ticket_for_planner(_ticket())
    assert compact is not None
    assert compact["ticket_id"] == "PLAT-1234"
    assert compact["acceptance_criteria"]
    assert len(compact["problem_statement"]) <= 600
    # Noise the planner never needs to plan a fresh change.
    for noise in ("assignee", "reporter", "attachments", "comments", "dependencies"):
        assert noise not in compact


def test_retry_summary_collapses_history_without_plans():
    summary = retry_summary_for_planner([_previous_attempt_with_full_plan()])
    assert summary is not None
    assert summary["num_previous_attempts"] == 1
    assert summary["last_failed_phase"] == "IMPLEMENTATION"
    assert summary["last_files_attempted"] == ["dao/user.py", "dao/other.py"]
    dumped = str(summary)
    assert "OLD_STRING_SENTINEL_should_not_leak" not in dumped
    assert "NEW_STRING_SENTINEL_should_not_leak" not in dumped
    assert "NOTE_SENTINEL_should_not_leak" not in dumped


def test_retry_summary_none_when_no_attempts():
    assert retry_summary_for_planner([]) is None
    assert retry_summary_for_planner(None) is None


def test_planner_prompt_excludes_previous_plans_and_ticket_noise():
    report = load_research_report(FIXTURE_REPORT)
    prompt = build_planner_prompt(
        report,
        str(REPO_ROOT),
        previous_attempts=[_previous_attempt_with_full_plan()],
        ticket=_ticket(),
        git_target_branch="main",
        repo_type="python",
    )
    # Research handoff + retry summary surface; raw history does not.
    assert "research_output" in prompt
    assert "retry_summary" in prompt
    assert "OLD_STRING_SENTINEL_should_not_leak" not in prompt
    assert "NEW_STRING_SENTINEL_should_not_leak" not in prompt
    assert "NOTE_SENTINEL_should_not_leak" not in prompt
    # Compact ticket present; PII / triage noise absent.
    assert "acceptance_criteria" in prompt
    assert "security.team" not in prompt
    assert "pen.test" not in prompt
    assert "main" in prompt


def test_research_handoff_keeps_hypotheses_drops_issue_type():
    report = load_research_report(FIXTURE_REPORT)
    handoff = research_handoff_for_planner(report)
    assert handoff["hypotheses"]  # planner reasons from these
    assert "affected_files" in handoff
    assert "module_blast_radius" in handoff
    assert "issue_type_assessment" not in handoff


# ── Research ─────────────────────────────────────────────────────────────────


def test_compact_repo_profile_drops_root_and_package_roots():
    profile = {
        "repo_type": "python",
        "root_path": "/tmp/secret/path",
        "modules": [str(i) for i in range(20)],
        "package_roots": ["src/main/java"],
    }
    compact = _compact_repo_profile(profile)
    assert compact == {"repo_type": "python", "modules": [str(i) for i in range(8)]}


def test_compact_security_enrichment_keeps_signal_drops_noise():
    enrichment = {
        "severity": "High",
        "vulnerability_type": "SQLi",
        "repository": "platform/auth-service",
        "target_branch": "main",
        "description": "x" * 2000,
        "flags": ["a", "b"],
        "internal_scan_id": "SHOULD_NOT_LEAK",
        "raw_payload": {"huge": "blob"},
    }
    compact = _compact_security_enrichment(enrichment)
    assert compact["severity"] == "High"
    assert compact["vulnerability_type"] == "SQLi"
    assert len(compact["description"]) <= 800
    assert "internal_scan_id" not in compact
    assert "raw_payload" not in compact


def test_research_message_uses_compact_profile(tmp_path):
    ticket = _ticket()
    ticket["ticket_history_summary"] = {
        "security_enrichment": {
            "severity": "High",
            "vulnerability_type": "SQLi",
            "internal_scan_id": "SHOULD_NOT_LEAK",
        }
    }
    message = build_research_user_message(
        ticket,
        str(tmp_path),
        {
            "repo_type": "python",
            "root_path": "/tmp/secret/path",
            "modules": ["."],
            "package_roots": ["src"],
        },
        ["search"],
        "",
        InMemoryResearchMemory(),
    )
    assert "SHOULD_NOT_LEAK" not in message
    assert "package_roots" not in message
    assert "High" in message
