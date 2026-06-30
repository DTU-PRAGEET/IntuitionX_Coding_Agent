"""Tests for skills loader and Research skill selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.skills_loader import (
    load_reference,
    load_skill_file,
    load_skill_manifest,
    select_references,
    select_research_skill,
)
from tests.helpers import make_bug_ticket, make_security_ticket

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills" / "research"
SKILL_DIR = SKILLS_ROOT
SECURITY_REF = SKILLS_ROOT / "references" / "security_patterns.md"


def test_load_skill_manifest_research():
    manifest = load_skill_manifest(SKILL_DIR)
    assert manifest.name == "research"
    assert manifest.description
    assert manifest.tool_budget == 12
    assert manifest.recursion_limit >= 28


def test_load_security_reference():
    content = load_reference(SKILL_DIR, "security_patterns")
    assert "security_enrichment" in content


def test_malformed_skill_missing_script_name(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text(
        "script_description: missing name\nscript_content: |\n  body\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bad.md"):
        load_skill_file(bad)


def test_security_ticket_selects_security_skill():
    ticket = make_security_ticket("SEC-TEST-1")
    skill, reason = select_research_skill(ticket, SKILLS_ROOT)
    assert skill is not None
    assert skill.name == "security_research"
    assert reason == "label_match"
    assert SECURITY_REF.exists()


def test_select_references_security():
    ticket = make_security_ticket("SEC-TEST-1")
    refs = select_references(ticket, SKILL_DIR, "research")
    assert any(r.name == "security_patterns.md" for r in refs)


def test_bug_ticket_no_skill_match():
    ticket = make_bug_ticket("PLAT-9999")
    skill, reason = select_research_skill(ticket, SKILLS_ROOT)
    assert skill is None
    assert reason == "no_skill_match"


def test_research_dry_run_includes_skill_fields(tmp_path):
    from unittest.mock import patch

    from src.agents.research.__main__ import main

    ticket = make_security_ticket("SEC-TEST-1")
    with (
        patch("src.agents.research.__main__.credentials_configured", return_value=True),
        patch("src.agents.research.__main__.fetch_jira_ticket", return_value=ticket),
    ):
        code = main(["--repo-path", str(tmp_path), "--dry-run"])
    assert code == 0
