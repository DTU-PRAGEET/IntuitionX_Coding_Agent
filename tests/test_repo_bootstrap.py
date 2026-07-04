"""Tests for repo bootstrap context used in research prompts."""

from __future__ import annotations

from pathlib import Path

from src.agents.research.research_agent import _sanitize_research_report
from src.agents.research.tools.repo_profile import bootstrap_repo_context
from src.core.schemas import ResearchReport


def test_bootstrap_repo_context_existing_repo(tmp_path: Path) -> None:
    """Test Bootstrap Repo Context Existing Repo for this module."""
    (tmp_path / "sqli").mkdir()
    (tmp_path / "run.py").write_text("print('ok')\n", encoding="utf-8")

    ctx = bootstrap_repo_context(str(tmp_path))

    assert ctx["exists"] is True
    assert ctx["file_count_estimate"] >= 2
    assert "run.py" in ctx["top_level_entries"]


def test_sanitize_research_report_strips_false_repo_blocker(tmp_path: Path) -> None:
    """Test Sanitize Research Report Strips False Repo Blocker for this module."""
    report = ResearchReport(
        ticket_id="SPACE-1",
        research_summary="Repository is not checked out at repo_path",
        hypotheses=[
            {
                "rank": 1,
                "hypothesis": "repo missing",
                "evidence_strength": "weak",
                "evidence": [],
            }
        ],
        affected_files=[],
        affected_modules=[],
        module_blast_radius=[],
        architectural_risk="high",
        issue_type_assessment="unknown",
        flags=["BLOCKER: repo_path_not_found (/tmp/demo)"],
    )

    cleaned = _sanitize_research_report(report, str(tmp_path))

    assert not any("repo_path_not_found" in flag for flag in cleaned.flags)
    assert "auto-corrected" in cleaned.research_summary.lower()


def test_sanitize_research_report_strips_not_accessible_blocker(tmp_path: Path) -> None:
    """Test Sanitize Research Report Strips Not Accessible Blocker for this module."""
    report = ResearchReport(
        ticket_id="SPACE-129069",
        research_summary="Repository path in ticket context is not accessible in this environment",
        hypotheses=[
            {
                "rank": 1,
                "hypothesis": "XSS via Jinja2",
                "evidence_strength": "strong",
                "evidence": ["sqli/app.py"],
            }
        ],
        affected_files=[],
        affected_modules=[],
        module_blast_radius=[],
        architectural_risk="high",
        issue_type_assessment="code_bug",
        flags=[
            "BLOCKER: Repo path in ticket context is not accessible in this environment; "
            "cannot apply or verify fixes."
        ],
    )

    cleaned = _sanitize_research_report(report, str(tmp_path))

    assert not any("not accessible" in flag.lower() for flag in cleaned.flags)


def test_research_agent_fails_fast_when_repo_missing(tmp_path: Path) -> None:
    """Test Research Agent Fails Fast When Repo Missing for this module."""
    from src.agents.research.research_agent import ResearchAgent
    from tests.helpers import make_security_ticket

    missing = tmp_path / "does-not-exist"
    ticket = make_security_ticket("SEC-MISS")
    agent = ResearchAgent(str(missing))

    try:
        agent.run(ticket)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "does not exist on disk" in str(exc)
