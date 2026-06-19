"""Tests for skills loader and Research skill selection."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.agents.orchestrator.tools.jira import _mock_jira_ticket, mock_security_jira_ticket
from src.core.skills_loader import load_skill_file, select_research_skill

SECURITY_MD = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agents"
    / "research"
    / "skills"
    / "security.md"
)
SKILLS_DIR = SECURITY_MD.parent
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_parse_valid_security_md():
    skill = load_skill_file(SECURITY_MD)
    assert skill.name == "security_research"
    assert skill.description
    assert skill.content.strip()
    assert "security_enrichment" in skill.content


def test_malformed_skill_missing_script_name(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "script_description: missing name\nscript_content: |\n  body\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bad.md"):
        load_skill_file(bad)


def test_mock_security_ticket_selects_security_skill():
    ticket = mock_security_jira_ticket("SEC-4421")
    skill, reason = select_research_skill(ticket, SKILLS_DIR)
    assert skill is not None
    assert skill.name == "security_research"
    assert reason == "label_match"


def test_mock_bug_ticket_no_skill_match():
    ticket = _mock_jira_ticket("PLAT-9999")
    skill, reason = select_research_skill(ticket, SKILLS_DIR)
    assert skill is None
    assert reason == "no_skill_match"


def test_research_dry_run_includes_skill_fields(tmp_path):
    """Subprocess run — structlog writes to real stderr, not pytest capsys."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.agents.research",
            "--mock-security",
            "--repo-path",
            str(tmp_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["loaded_skill"] == "security_research"
    assert payload["skill_reason"] == "label_match"
    assert "[skills_loader]" in result.stderr
    assert "loaded=security" in result.stderr
