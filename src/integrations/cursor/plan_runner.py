"""Cursor-backed planner — ResearchOutput → FixPlan (milestone 1.3)."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.logging import get_logger
from src.core.runner_utils import parse_llm_json
from src.core.schemas import FixPlan, FixTarget, ResearchReport
from src.core.skill_registry import SkillRegistry, get_skill_registry
from src.core.state import ResearchOutput
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway

_log = get_logger("cursor_plan")

_FIXPLAN_JSON_SCHEMA = """
{
  "ticket_id": "string",
  "root_cause": "string",
  "primary_fix_type": "parameterize_sql|sanitize_input|auth_check|dependency_upgrade|secrets_removal|xss_encoding|other",
  "targets": [{"file_path": "path.py", "start_line": 42, "end_line": 55, "fix_type": "parameterize_sql", "rationale": "..."}],
  "files_to_change": ["path.py"],
  "recommendations": ["Use parameterized queries"],
  "evidence": ["Line 42 concatenates user input"],
  "flags": []
}
"""


def _build_plan_prompt(research: ResearchOutput, repo_path: str) -> str:
    compact = {
        "ticket_id": research.get("ticket_id"),
        "research_summary": (research.get("research_summary") or "")[:800],
        "affected_files": research.get("affected_files") or [],
        "architectural_risk": research.get("architectural_risk"),
        "flags": research.get("flags") or [],
    }
    return f"""Given this research output, produce a FixPlan (NOT executable patches).

Rules:
- Target specific files and line ranges from evidence
- Choose primary_fix_type and per-target fix_type from: parameterize_sql, sanitize_input, auth_check, dependency_upgrade, secrets_removal, xss_encoding, other
- Do NOT write old_string/new_string patches
- files_to_change must list every file you target

Repo path: {repo_path}
Research JSON:
{json.dumps(compact, separators=(",", ":"))}

Return ONLY valid JSON matching this schema (no markdown fences):
{_FIXPLAN_JSON_SCHEMA}
"""


def mock_fix_plan(research: ResearchOutput) -> FixPlan:
    """Deterministic FixPlan for dry-run / tests."""
    ticket_id = research.get("ticket_id") or "UNKNOWN"
    files = research.get("affected_files") or []
    primary_path = files[0]["path"] if files else "src/demo/example.py"
    fix_type = "parameterize_sql"
    if any("xss" in (research.get("research_summary") or "").lower() for _ in [1]):
        fix_type = "xss_encoding"
    return FixPlan(
        ticket_id=ticket_id,
        root_cause=(research.get("research_summary") or "Unknown root cause")[:500],
        primary_fix_type=fix_type,  # type: ignore[arg-type]
        targets=[
            FixTarget(
                file_path=primary_path,
                start_line=40,
                end_line=60,
                fix_type=fix_type,  # type: ignore[arg-type]
                rationale="Replace dynamic SQL / unsafe input handling with parameterized query",
            )
        ],
        files_to_change=[primary_path],
        recommendations=["Use parameterized queries", "Validate input length"],
        evidence=[
            f.get("reason", "")[:200]
            for f in files[:3]
            if isinstance(f, dict) and f.get("reason")
        ],
        flags=["cursor_plan_dry_run"],
    )


def fix_plan_to_plan_output(fix_plan: FixPlan) -> dict:
    """Map FixPlan to legacy plan_output shape for implement stub / executor."""
    steps = []
    for i, target in enumerate(fix_plan.targets, start=1):
        steps.append(
            {
                "step_id": i,
                "action": "READ",
                "file": target.file_path,
                "target": f"Read lines {target.start_line}-{target.end_line} before fix",
                "reason": target.rationale,
                "validate": "File read succeeds",
            }
        )
    return {
        "ticket_id": fix_plan.ticket_id,
        "root_hypothesis": fix_plan.root_cause,
        "files_to_change": fix_plan.files_to_change,
        "primary_fix_type": fix_plan.primary_fix_type,
        "targets": [t.model_dump() for t in fix_plan.targets],
        "execution_steps": steps,
        "recommendations": fix_plan.recommendations,
        "risk_level": "medium",
        "flags": fix_plan.flags,
    }


def run_cursor_plan(
    research: ResearchOutput,
    repo_path: str,
    *,
    dry_run: bool = False,
    gateway: CursorGateway | None = None,
    skill_registry: SkillRegistry | None = None,
    ticket: dict | None = None,
) -> FixPlan:
    """Plan via CursorGateway — ResearchOutput → FixPlan."""
    root = Path(repo_path)
    if not root.is_dir():
        raise ValueError(f"repo_path is not a directory: {repo_path}")

    if dry_run:
        return mock_fix_plan(research)

    registry = skill_registry or get_skill_registry()
    skills = registry.select("planner", ticket=ticket)  # type: ignore[arg-type]
    skill_block = SkillRegistry.format_for_prompt(skills)

    gw = gateway or get_cursor_gateway(root, dry_run=False)
    prompt = _build_plan_prompt(research, repo_path)
    if skill_block:
        prompt = f"{prompt}\n\n{skill_block}"
    run = gw.ask(prompt)
    if run.dry_run or not run.text.strip():
        return mock_fix_plan(research)

    try:
        plan = parse_llm_json(run.text, FixPlan)
        plan.flags = list(dict.fromkeys(list(plan.flags) + ["cursor_plan"]))
        if not plan.files_to_change and plan.targets:
            plan.files_to_change = list(dict.fromkeys(t.file_path for t in plan.targets))
        return plan
    except Exception as e:
        _log.warning("cursor_plan_parse_failed", error=str(e))
        fallback = mock_fix_plan(research)
        fallback.flags.append(f"parse_fallback: {e}")
        return fallback


def research_output_to_report(research: ResearchOutput) -> ResearchReport:
    """Convert ResearchOutput TypedDict to ResearchReport for validation helpers."""
    return ResearchReport.model_validate(dict(research))
