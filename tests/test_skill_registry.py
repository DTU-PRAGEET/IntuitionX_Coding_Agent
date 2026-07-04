"""Tests for SkillRegistry — runtime skill discovery and selection."""

from __future__ import annotations

from pathlib import Path

from src.core.schemas import FixPlan, FixTarget
from src.core.skill_manager import SkillRegistry, default_skills_root
from tests.helpers import make_security_ticket

SKILLS_ROOT = default_skills_root()


def test_registry_discovers_reference_skills():
    """Test Registry Discovers Reference Skills for this module."""
    reg = SkillRegistry(SKILLS_ROOT)
    names = {s.name for s in reg.all_skills()}
    assert "java-sast-fixes" in names
    assert "java-sca-fixes" in names
    assert "research" in names


def test_registry_loads_skill_sections():
    """Test Registry Loads Skill Sections for this module."""
    reg = SkillRegistry(SKILLS_ROOT)
    skill = reg.get("java-sast-fixes")
    assert skill is not None
    assert skill.section("implementation_guidance")
    assert skill.section("test_cases")


def test_select_research_base_skill():
    """Test Select Research Base Skill for this module."""
    reg = SkillRegistry(SKILLS_ROOT)
    ticket = make_security_ticket("SEC-SK1")
    selected = reg.select("research", ticket=ticket)
    names = [s.name for s in selected]
    assert "research" in names


def test_select_implement_security_skill_for_sast_ticket():
    """Test Select Implement Security Skill For Sast Ticket for this module."""
    reg = SkillRegistry(SKILLS_ROOT)
    ticket = make_security_ticket("SEC-SK2")
    fix = FixPlan(
        ticket_id="SEC-SK2",
        root_cause="SQL injection",
        primary_fix_type="parameterize_sql",
        targets=[
            FixTarget(
                file_path="src/Dao.java",
                start_line=10,
                end_line=20,
                fix_type="parameterize_sql",
                rationale="Fix SQLi",
            )
        ],
        files_to_change=["src/Dao.java"],
    )
    selected = reg.select("implement", ticket=ticket, fix_plan=fix)
    names = [s.name for s in selected]
    assert "executor" in names
    assert "java-sast-fixes" in names


def test_format_for_prompt_includes_guidance():
    """Test Format For Prompt Includes Guidance for this module."""
    reg = SkillRegistry(SKILLS_ROOT)
    skill = reg.get("java-sast-fixes")
    assert skill is not None
    text = SkillRegistry.format_for_prompt([skill])
    assert "java-sast-fixes" in text
    assert "implementation_guidance" in text.lower() or "SQL Injection" in text
