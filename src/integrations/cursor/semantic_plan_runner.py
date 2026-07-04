"""Cursor-backed semantic planner — ResearchReport → ExecutionPlan (single ask)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.logging import get_logger
from src.core.planner_context import (
    compact_ticket_for_planner,
    research_handoff_for_planner,
    retry_summary_for_planner,
)
from src.core.runner_utils import parse_llm_json
from src.core.schemas import ExecutionPlan, ExecutionStep, ResearchReport
from src.core.skill_manager import PromptBuilder, format_domain_skills_for_prompt, module_skills_dir
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway

_log = get_logger("semantic_plan")

SKILLS_DIR = module_skills_dir("planner")

_SEMANTIC_EXECUTION_PLAN_SCHEMA = """
{
  "ticket_id": "string",
  "root_hypothesis": "string",
  "files_to_change": ["relative/path.py"],
  "implementation_constraints": ["string"],
  "execution_steps": [
    {
      "step_id": 1,
      "action": "READ",
      "file": "src/auth.py",
      "target": "Read validate_user before edit",
      "reason": "Ground context",
      "validate": "File read succeeds"
    },
    {
      "step_id": 2,
      "action": "EDIT",
      "file": "src/auth.py",
      "target": "Parameterize JWT secret",
      "reason": "Remove committed secret",
      "validate": "grep for os.environ",
      "target_type": "function",
      "target_name": "validate_user",
      "approximate_line": 120,
      "instruction": "Replace the hardcoded JWT secret with an environment variable.",
      "intent_marker": "os.environ",
      "change": "Use env var for JWT secret"
    },
    {
      "step_id": 3,
      "action": "VERIFY",
      "file": "src/auth.py",
      "target": "Confirm fix applied",
      "reason": "Ensure secret removed",
      "validate": "grep for os.environ",
      "intent_marker": "os.environ"
    }
  ],
  "write_tests": false,
  "create_files": false,
  "risk_level": "medium",
  "self_healing_notes": [],
  "flags": []
}
"""


def build_semantic_plan_prompt(
    report: ResearchReport,
    repo_path: str,
    *,
    routing_route: str = "FULL",
    previous_attempts: list[dict[str, Any]] | None = None,
    gate_feedback: str | None = None,
    ticket: dict[str, Any] | None = None,
    git_target_branch: str | None = None,
    repo_type: str | None = None,
) -> str:
    """Build prompt for Cursor to explore repo and emit semantic ExecutionPlan."""
    context: dict[str, Any] = {
        "research_output": research_handoff_for_planner(report),
        "repository": {
            "repo_path": repo_path,
            "target_branch": git_target_branch,
            "repo_type": repo_type,
        },
        "routing_route": routing_route,
    }
    compact_ticket = compact_ticket_for_planner(ticket)
    if compact_ticket:
        context["ticket"] = compact_ticket
    retry_summary = retry_summary_for_planner(previous_attempts)
    if retry_summary:
        context["retry_summary"] = retry_summary
    if gate_feedback:
        context["gate_feedback"] = gate_feedback

    prompt, _manifest = PromptBuilder.build(
        "planner",
        SKILLS_DIR,
        context,
        include_references=False,
        extra_instructions=(
            "Explore the repository at the given repo_path. Read affected files on disk. "
            "Produce an ExecutionPlan JSON with semantic EDIT steps — do NOT use old_string "
            "or new_string patches.\n\n"
            "Every EDIT step MUST include: file, target, reason, validate, target_type, "
            "target_name (except file/block targets), instruction, intent_marker.\n"
            "target_type must be one of: function, method, class, variable, assignment, "
            "import, block, file.\n"
            "Use approximate_line as a hint from your exploration. For block targets, "
            "include end_line.\n"
            "Every EDIT must be preceded by READ and followed by VERIFY on the same file.\n"
            "Use actions READ|EDIT|VERIFY|RUN_TESTS only.\n\n"
            f"Return ONLY valid JSON matching this schema (no markdown fences):\n"
            f"{_SEMANTIC_EXECUTION_PLAN_SCHEMA}"
        ),
    )
    domain_skills = format_domain_skills_for_prompt("planner", ticket)
    if domain_skills:
        prompt = f"{prompt}\n\n{domain_skills}"
    return prompt


def mock_semantic_plan(report: ResearchReport, repo_path: str) -> ExecutionPlan:
    """Deterministic semantic ExecutionPlan for dry-run / tests without Cursor."""
    files = [f.path for f in report.affected_files if f.path]
    primary = files[0] if files else "src/demo/example.py"
    hypothesis = (
        report.hypotheses[0].hypothesis
        if report.hypotheses
        else (report.research_summary or "Fix per research")[:500]
    )
    steps: list[ExecutionStep] = [
        ExecutionStep(
            step_id=1,
            action="READ",
            file=primary,
            target=f"Read {primary} before edit",
            reason="Ground context from disk",
            validate="File read succeeds",
        ),
        ExecutionStep(
            step_id=2,
            action="EDIT",
            file=primary,
            target="Apply security fix",
            reason=hypothesis[:200],
            validate="grep for intent marker",
            target_type="block",
            target_name="",
            approximate_line=1,
            end_line=10,
            instruction=f"Apply fix for: {hypothesis[:300]}",
            intent_marker="FIXED",
            change="Security remediation",
        ),
        ExecutionStep(
            step_id=3,
            action="VERIFY",
            file=primary,
            target="Confirm fix",
            reason="Ensure remediation applied",
            validate="grep for FIXED",
            intent_marker="FIXED",
        ),
    ]
    return ExecutionPlan(
        ticket_id=report.ticket_id,
        root_hypothesis=hypothesis[:500],
        files_to_change=files or [primary],
        implementation_constraints=["Do not modify unrelated files"],
        execution_steps=steps,
        write_tests=False,
        create_files=False,
        risk_level=report.architectural_risk,
        self_healing_notes=[],
        flags=["semantic_plan_dry_run"],
    )


def run_semantic_plan(
    report: ResearchReport,
    repo_path: str,
    *,
    dry_run: bool = False,
    gateway: CursorGateway | None = None,
    routing_route: str = "FULL",
    previous_attempts: list[dict[str, Any]] | None = None,
    gate_feedback: str | None = None,
    ticket: dict[str, Any] | None = None,
    git_target_branch: str | None = None,
    repo_type: str | None = None,
) -> ExecutionPlan:
    """Single Cursor ask — explore repo and return semantic ExecutionPlan."""
    root = Path(repo_path)
    if not root.is_dir():
        raise ValueError(f"repo_path is not a directory: {repo_path}")

    if dry_run:
        return mock_semantic_plan(report, repo_path)

    gw = gateway or get_cursor_gateway(root, dry_run=False)
    prompt = build_semantic_plan_prompt(
        report,
        repo_path,
        routing_route=routing_route,
        previous_attempts=previous_attempts,
        gate_feedback=gate_feedback,
        ticket=ticket,
        git_target_branch=git_target_branch,
        repo_type=repo_type,
    )

    _log.info("semantic_plan_start", ticket_id=report.ticket_id, repo_path=repo_path)
    run = gw.ask(prompt)
    if run.dry_run or not (run.text or "").strip():
        plan = mock_semantic_plan(report, repo_path)
        plan.flags.append("cursor_semantic_plan_skipped")
        return plan

    try:
        plan = parse_llm_json(run.text, ExecutionPlan)
        plan.flags = list(dict.fromkeys(list(plan.flags) + ["cursor_semantic_plan"]))
        return plan
    except Exception as exc:
        _log.warning("semantic_plan_parse_failed", error=str(exc))
        fallback = mock_semantic_plan(report, repo_path)
        fallback.flags.append(f"parse_fallback: {exc}")
        return fallback
