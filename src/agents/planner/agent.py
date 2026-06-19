"""Planning Agent — single-turn structured planner with bounded verification tools."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import traceable
from pydantic import ValidationError

from src.agents.planner.tools.builder import build_planner_tools
from src.core.llm import TaskComplexity, get_llm
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import llm_content, parse_llm_json
from src.core.schemas import ExecutionPlan, ResearchReport
# from src.core.debug_log import agent_debug
from src.core.state import AgentState, PlanOutput
from src.tools.registry import ToolRegistry

_log = get_logger("planner")

SKILLS_PATH = Path(__file__).parent / "skills" / "scripts.md"
MAX_VERIFICATION_CALLS = 3
_INVALID_WHERE = frozenset({".", ""})


def _is_valid_step_path(path: str, repo_path: str) -> bool:
    if not path or path in _INVALID_WHERE:
        return False
    if path == repo_path or path.rstrip("/") == repo_path.rstrip("/"):
        return False
    candidate = Path(path)
    if candidate.exists() and candidate.is_dir():
        return False
    return True


def _research_file_paths(report: ResearchReport) -> list[str]:
    return [f.path for f in report.affected_files if f.path]


def _normalize_plan_paths(plan: ExecutionPlan, report: ResearchReport, repo_path: str) -> ExecutionPlan:
    """Repair common LLM mistakes: where='.' and empty files_to_change."""
    candidates = _research_file_paths(report)
    if not plan.files_to_change and candidates:
        plan.files_to_change = list(dict.fromkeys(candidates))

    default_file = None
    if len(candidates) == 1:
        default_file = candidates[0]
    elif plan.files_to_change:
        default_file = plan.files_to_change[0]

    for step in plan.execution_steps:
        where = step.where or step.file
        if not _is_valid_step_path(where, repo_path) and default_file:
            step.where = default_file
            step.file = default_file
    return plan


def _validate_plan_executable(plan: ExecutionPlan, report: ResearchReport, repo_path: str) -> None:
    candidates = _research_file_paths(report)
    if candidates and not plan.files_to_change:
        raise ValueError(
            "Planner produced empty files_to_change but research has affected_files: "
            f"{candidates}"
        )
    if not plan.execution_steps:
        raise ValueError("Planner produced empty execution_steps")
    for step in plan.execution_steps:
        where = step.where or step.file
        if not _is_valid_step_path(where, repo_path):
            raise ValueError(
                f"Planner step {step.step_id} has invalid where={where!r} "
                f"(must be a file path, not '.' or repo root)"
            )


def _load_skills() -> str:
    return SKILLS_PATH.read_text(encoding="utf-8")


def load_research_report(path: Path) -> ResearchReport:
    """Load and validate ResearchReport JSON; raise ValidationError on bad input."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read research report {path}: {e}") from e
    try:
        return ResearchReport.model_validate_json(raw)
    except ValidationError as e:
        raise ValueError(f"Invalid ResearchReport in {path}: {e}") from e


def build_planner_prompt(
    report: ResearchReport,
    repo_path: str,
    *,
    routing_route: str = "FULL",
    previous_attempts: list[dict[str, Any]] | None = None,
) -> str:
    skills = _load_skills()
    context = {
        "research_output": report.model_dump(),
        "repo_path": repo_path,
        "routing_route": routing_route,
        "previous_attempts": previous_attempts or [],
    }
    return (
        f"{skills}\n\n## CONTEXT\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Produce the ExecutionPlan JSON with canonical fields on every step: "
        "why, where, what, validate. Every EDIT step MUST include old_string and new_string."
    )


def _validate_plan_files(plan: ExecutionPlan, repo_path: str | None) -> list[str]:
    flags: list[str] = []
    if not repo_path:
        return flags
    root = Path(repo_path)
    for fp in plan.files_to_change:
        if not (root / fp).exists() and not Path(fp).exists():
            flags.append(f"File not found during validation: {fp}")
    return flags


def _verify_plan_with_tools(
    plan: ExecutionPlan,
    registry: ToolRegistry,
    *,
    max_calls: int = MAX_VERIFICATION_CALLS,
) -> tuple[list[str], int]:
    """Run up to max_calls read/grep/find checks; not a ReAct loop."""
    flags: list[str] = []
    calls = 0
    seen_reads: set[str] = set()

    for step in plan.execution_steps:
        if calls >= max_calls:
            break
        path = step.where or step.file
        if not path:
            continue
        if not _is_valid_step_path(path, registry.cwd):
            flags.append(f"Skipped verification for invalid path: {path!r}")
            continue

        if step.action in ("LOCATE", "VERIFY", "EDIT") and path not in seen_reads:
            _log.info("verification", tool="read", step_id=step.step_id, path=path)
            try:
                result = registry.execute("read", path=path, start_line=1, end_line=200)
            except Exception as e:
                raise RuntimeError(
                    f"Planner verification read failed for step={step.step_id} path={path!r}: {e}"
                ) from e
            calls += 1
            seen_reads.add(path)
            if result.is_error:
                flags.append(f"Verification read failed for {path}: {result.output[:200]}")
            elif step.action == "EDIT" and step.old_string and step.old_string not in result.output:
                flags.append(f"EDIT step {step.step_id}: old_string not found in {path}")

        if calls >= max_calls:
            break
        if step.action == "SEARCH" and step.what:
            _log.info("verification", tool="grep", step_id=step.step_id, pattern=step.what, path=path)
            try:
                result = registry.execute("grep", pattern=step.what, path=path, limit=20)
            except Exception as e:
                raise RuntimeError(
                    f"Planner verification grep failed for step={step.step_id} path={path!r}: {e}"
                ) from e
            calls += 1
            if result.is_error:
                flags.append(f"Verification grep failed for {path}: {result.output[:200]}")

    return flags, calls


def _plan_to_output(plan: ExecutionPlan) -> PlanOutput:
    return PlanOutput(
        ticket_id=plan.ticket_id,
        root_hypothesis=plan.root_hypothesis,
        files_to_change=plan.files_to_change,
        implementation_constraints=plan.implementation_constraints,
        execution_steps=[s.model_dump() for s in plan.execution_steps],
        write_tests=plan.write_tests,
        create_files=plan.create_files,
        risk_level=plan.risk_level,
        self_healing_notes=plan.self_healing_notes,
        flags=plan.flags,
    )


@traceable(name="planner_agent")
def run_planner(
    report: ResearchReport,
    repo_path: str,
    *,
    routing_route: str = "FULL",
    previous_attempts: list[dict[str, Any]] | None = None,
    llm: BaseChatModel | None = None,
    verify: bool = True,
) -> ExecutionPlan:
    """Single LLM call to produce ExecutionPlan; optional bounded verification tools."""
    configure_logging()
    _log.info("start", ticket_id=report.ticket_id, repo_path=repo_path, routing_route=routing_route)

    llm = llm or get_llm(TaskComplexity.HIGH)
    prompt = build_planner_prompt(
        report, repo_path, routing_route=routing_route, previous_attempts=previous_attempts
    )

    registry: ToolRegistry | None = None
    try:
        try:
            response = llm.invoke(prompt)
            raw = llm_content(response)
            plan = parse_llm_json(raw, ExecutionPlan)
            # agent_debug(
            #     "B",
            #     "planner/agent.py:run_planner",
            #     "raw_plan_before_normalize",
            #     {
            #         "files_to_change": plan.files_to_change,
            #         "steps": [
            #             {"step_id": s.step_id, "action": s.action, "where": s.where or s.file}
            #             for s in plan.execution_steps
            #         ],
            #     },
            # )
            plan = _normalize_plan_paths(plan, report, repo_path)
            _validate_plan_executable(plan, report, repo_path)
            # agent_debug(
            #     "B",
            #     "planner/agent.py:run_planner",
            #     "plan_after_normalize",
            #     {
            #         "files_to_change": plan.files_to_change,
            #         "steps": [
            #             {"step_id": s.step_id, "action": s.action, "where": s.where or s.file}
            #             for s in plan.execution_steps
            #         ],
            #     },
            # )
        except ValidationError as e:
            _log.error("plan_validation_failed", error=str(e))
            raise ValueError(f"Planner output failed schema validation: {e}") from e
        except Exception as e:
            _log.error("llm_failed", error=str(e))
            raise RuntimeError(
                f"Planner LLM failed for ticket={report.ticket_id!r} repo={repo_path!r}: {e}"
            ) from e

        validation_flags = _validate_plan_files(plan, repo_path)
        if verify:
            registry, _ = build_planner_tools(repo_path)
            verify_flags, tool_calls = _verify_plan_with_tools(plan, registry)
            validation_flags.extend(verify_flags)
            _log.info("verification_complete", tool_calls=tool_calls)

        if validation_flags:
            plan.flags = list(dict.fromkeys(list(plan.flags) + validation_flags))

        if previous_attempts:
            for att in previous_attempts[-3:]:
                note = f"Avoid: {(att.get('error_summary') or '')[:100]}"
                if note not in plan.self_healing_notes:
                    plan.self_healing_notes.append(note)

        _log.info(
            "complete",
            ticket_id=plan.ticket_id,
            steps=len(plan.execution_steps),
            files=len(plan.files_to_change),
        )
        return plan
    finally:
        if registry is not None:
            registry.dispose()


@traceable(name="Planner Agent — TODO Generation")
def planner_node(state: AgentState) -> AgentState:
    now = datetime.now(timezone.utc).isoformat()
    research_raw = state.get("research_output")
    if not research_raw:
        return {
            **state,
            "current_phase": "PLANNING",
            "exit_message": "Planner input missing: research_output",
            "updated_at": now,
        }

    try:
        report = ResearchReport.model_validate(research_raw)
    except ValidationError as e:
        return {
            **state,
            "current_phase": "PLANNING",
            "exit_message": f"Planner input validation failed: {e}",
            "updated_at": now,
        }

    repo_path = state.get("repo_path") or "."
    try:
        plan = run_planner(
            report,
            repo_path,
            routing_route=state.get("routing_route") or "FULL",
            previous_attempts=state.get("previous_attempts"),
        )
        plan_output = _plan_to_output(plan)
    except Exception as e:
        return {
            **state,
            "current_phase": "PLANNING",
            "exit_message": f"Planner failed: {e}",
            "updated_at": now,
        }

    return {
        **state,
        "plan_output": plan_output,
        "current_phase": "PLANNING",
        "prompt_version": "planner-v1.0",
        "updated_at": now,
    }


def plan_hash(plan: PlanOutput) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()[:16]
