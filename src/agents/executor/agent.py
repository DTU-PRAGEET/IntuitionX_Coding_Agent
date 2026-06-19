"""Executor Agent — deterministic-first step runner with LOW-tier LLM fallback on EDIT."""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import traceable
from pydantic import BaseModel, ValidationError

from src.agents.executor.tools.builder import build_executor_tools
from src.core.llm import TaskComplexity, get_llm
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import llm_content
from src.core.schemas import ExecutionPlan, ToolResult
from src.core.state import AgentState, ExecutionOutput
from src.memory.cache import save_failed_attempt
from src.tools.registry import ToolRegistry

_log = get_logger("executor")

SKILLS_PATH = Path(__file__).parent / "skills" / "scripts.md"


class ExecutionResult(BaseModel):
    ticket_id: str
    changed_files: list[str]
    steps_completed: int
    step_results: list[dict[str, Any]]
    plan_deviations: list[str] = []


def _load_skills() -> str:
    return SKILLS_PATH.read_text(encoding="utf-8")


def load_execution_plan(path: Path) -> ExecutionPlan:
    """Load and validate ExecutionPlan JSON; raise ValueError on bad input."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read execution plan {path}: {e}") from e
    try:
        return ExecutionPlan.model_validate_json(raw)
    except ValidationError as e:
        raise ValueError(f"Invalid ExecutionPlan in {path}: {e}") from e


def _step_path(step: dict[str, Any]) -> str:
    return step.get("where") or step.get("file") or ""


def _step_to_dict(step: Any) -> dict[str, Any]:
    if hasattr(step, "model_dump"):
        return step.model_dump(by_alias=True)
    return dict(step)


def _tool_ok(result: ToolResult) -> tuple[bool, str]:
    return not result.is_error, result.output


def _log_step(step_id: int, action: str, file_path: str) -> None:
    _log.info("step", step=step_id, action=action, file=file_path)


def _log_step_result(step_id: int, ok: bool, output: str) -> None:
    _log.info("result", step=step_id, status="ok" if ok else "failed", output=output[:500])


def _execute_locate(registry: ToolRegistry, step: dict[str, Any]) -> tuple[bool, str]:
    path = _step_path(step)
    quoted = shlex.quote(path)
    result = registry.execute("bash", command=f"test -f {quoted}")
    return _tool_ok(result)


def _execute_search(registry: ToolRegistry, step: dict[str, Any]) -> tuple[bool, str]:
    pattern = step.get("what") or step.get("target") or ""
    path = _step_path(step) or "."
    cmd = f"grep -rn {shlex.quote(pattern)} {shlex.quote(path)}"
    result = registry.execute("bash", command=cmd)
    return _tool_ok(result)


def _is_human_verify_text(text: str) -> bool:
    lower = text.lower().strip()
    return lower.startswith(
        ("confirm", "verify", "ensure", "check", "validate", "grep shows")
    )


def _extract_grep_patterns_from_validate(validate: str) -> tuple[list[str], list[str]]:
    """Pull required/forbidden tokens from validate — only explicit grep-for segments."""
    if not validate:
        return [], []
    required: list[str] = []
    forbidden: list[str] = []
    for segment in re.split(r"[;\n|]+", validate):
        segment = segment.strip()
        if not segment:
            continue
        match = re.search(
            r"grep(?:\s+for|\s+shows)\s+(.+?)(?:\s+or\s+equivalent|\s+present|\s*$)",
            segment,
            re.I,
        )
        if not match:
            continue
        token = match.group(1).strip().strip("\"'")
        if not token or _is_human_verify_text(token):
            continue
        if token.startswith("!"):
            forbidden.append(token[1:].strip())
        else:
            required.append(token)
    return required, forbidden


def _meaningful_patch_lines(text: str) -> list[str]:
    """Non-comment, non-trivial lines from a patch block."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 4 or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def _read_file_snippet(registry: ToolRegistry, path: str, *, max_lines: int = 200) -> tuple[bool, str]:
    result = registry.execute("bash", command=f"head -n {max_lines} {shlex.quote(path)}")
    if result.is_error:
        return False, result.output
    return True, result.output


def _patterns_from_prior_edits(plan: ExecutionPlan, path: str, step_id: int) -> list[str]:
    """Use distinctive lines from the latest prior EDIT new_string on the same file."""
    latest: dict[str, Any] | None = None
    latest_id = -1
    for raw in plan.execution_steps:
        prior = _step_to_dict(raw)
        if prior.get("action") != "EDIT":
            continue
        if _step_path(prior) != path:
            continue
        prior_id = prior.get("step_id", 0)
        if prior_id >= step_id:
            continue
        if prior_id > latest_id:
            latest = prior
            latest_id = prior_id
    if not latest:
        return []
    return _meaningful_patch_lines(latest.get("new_string") or "")


def _validate_edit_completeness(
    registry: ToolRegistry,
    step: dict[str, Any],
) -> tuple[bool, str, list[str]]:
    """Ensure planned new_string lines are present and old_string lines are removed."""
    path = _step_path(step)
    planned_new = step.get("new_string") or ""
    planned_old = step.get("old_string") or ""
    if not planned_new:
        return True, "no planned patch", []

    ok, content = _read_file_snippet(registry, path)
    if not ok:
        return False, content, [content]

    missing = [
        ln
        for ln in _meaningful_patch_lines(planned_new)
        if not any(ln in file_line for file_line in content.splitlines())
    ]
    remaining = [
        ln
        for ln in _meaningful_patch_lines(planned_old)
        if any(ln in file_line for file_line in content.splitlines())
    ]
    errors: list[str] = []
    if missing:
        errors.append(f"planned new_string lines missing: {missing}")
    if remaining:
        errors.append(f"planned old_string lines still present: {remaining}")
    if errors:
        return False, "; ".join(errors), errors
    return True, "edit matches plan", []


def _llm_edit_fallback(
    registry: ToolRegistry,
    step: dict[str, Any],
    *,
    llm: BaseChatModel,
    file_content: str,
    extra_context: str = "",
) -> tuple[bool, str]:
    path = _step_path(step)
    fallback_llm = llm or get_llm(TaskComplexity.LOW)
    skills = _load_skills()
    prompt = f"""{skills}

## EDIT FALLBACK
File: {path}
Change required: {step.get('change') or step.get('why') or step.get('reason', '')}
Planned replacement (apply ALL of these lines — not a subset):
{step.get('new_string') or '(see change description)'}
Previous patch error: old_string missing, failed, incomplete, or not applied
{extra_context}

## FILE CONTENT
{file_content}

Return ONLY JSON: {{"old_string": "...", "new_string": "..."}}
Apply the FULL intended transformation from the plan — include every line from the planned replacement."""

    try:
        response = fallback_llm.invoke(prompt)
        content = llm_content(response)
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return False, "LLM did not return valid edit JSON"
        patch = json.loads(json_match.group())
        result = registry.execute(
            "edit",
            path=path,
            old_string=patch["old_string"],
            new_string=patch["new_string"],
        )
        return _tool_ok(result)
    except Exception as e:
        return False, f"Edit fallback failed: {e}"


def _forbidden_patterns_from_prior_edits(plan: ExecutionPlan, path: str, step_id: int) -> list[str]:
    """Lines from the latest prior EDIT old_string that must no longer appear after the fix."""
    latest: dict[str, Any] | None = None
    latest_id = -1
    for raw in plan.execution_steps:
        prior = _step_to_dict(raw)
        if prior.get("action") != "EDIT":
            continue
        if _step_path(prior) != path:
            continue
        prior_id = prior.get("step_id", 0)
        if prior_id >= step_id:
            continue
        if prior_id > latest_id:
            latest = prior
            latest_id = prior_id
    if not latest:
        return []
    return _meaningful_patch_lines(latest.get("old_string") or "")


def _resolve_verify_patterns(
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> tuple[list[str], list[str]]:
    """Build required/forbidden grep patterns for VERIFY."""
    path = _step_path(step)
    step_id = step.get("step_id", 0)
    validate = step.get("validate") or step.get("step_validate") or ""
    what = step.get("what") or step.get("target") or ""

    edit_patterns = _patterns_from_prior_edits(plan, path, step_id)
    validate_required, validate_forbidden = _extract_grep_patterns_from_validate(validate)

    if edit_patterns:
        # Prior EDIT new_string is the source of truth — ignore planner validate required patterns.
        required = list(edit_patterns)
    else:
        required = list(validate_required)
        if what and not _is_human_verify_text(what):
            if any(c in what for c in ("=", "%", "(", '"', "'", ".", "/")):
                required.append(what.strip())

    forbidden = list(
        dict.fromkeys(
            validate_forbidden
            + _forbidden_patterns_from_prior_edits(plan, path, step_id)
        )
    )
    required = list(dict.fromkeys(p for p in required if p))
    return required, forbidden


def _execute_verify(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> tuple[bool, str]:
    path = _step_path(step)
    patterns, forbidden = _resolve_verify_patterns(step, plan)

    if not patterns and not forbidden:
        _log.info("verification", mode="file_exists", path=path)
        result = registry.execute("bash", command=f"test -f {shlex.quote(path)}")
        return _tool_ok(result)

    errors: list[str] = []
    outputs: list[str] = []
    for pattern in patterns:
        cmd = f"grep -F {shlex.quote(pattern)} {shlex.quote(path)}"
        _log.info("verification", mode="grep", pattern=pattern[:120], path=path)
        result = registry.execute("bash", command=cmd)
        ok, out = _tool_ok(result)
        if ok:
            outputs.append(out.strip())
        else:
            errors.append(f"required pattern {pattern!r} not found")

    for pattern in forbidden:
        cmd = f"grep -F {shlex.quote(pattern)} {shlex.quote(path)}"
        _log.info("verification", mode="forbidden_grep", pattern=pattern[:120], path=path)
        result = registry.execute("bash", command=cmd)
        ok, _ = _tool_ok(result)
        if ok:
            errors.append(f"vulnerable pattern {pattern!r} still present")

    if errors:
        return False, "; ".join(errors)
    return True, outputs[0] if outputs else "verification passed"


def _execute_edit(
    registry: ToolRegistry,
    step: dict[str, Any],
    *,
    create_files: bool,
    llm: BaseChatModel,
) -> tuple[bool, str, list[str]]:
    path = _step_path(step)
    old_s = step.get("old_string")
    new_s = step.get("new_string")
    deviations: list[str] = []

    if not old_s and new_s and create_files:
        result = registry.execute("write", path=path, content=new_s)
        ok, out = _tool_ok(result)
        if ok:
            complete, msg, errs = _validate_edit_completeness(registry, step)
            if not complete:
                return False, msg, errs
            return ok, out, deviations

    used_fallback = False
    last_out = "EDIT missing patches"
    if old_s and new_s:
        result = registry.execute("edit", path=path, old_string=old_s, new_string=new_s)
        ok, last_out = _tool_ok(result)
        if ok:
            complete, msg, errs = _validate_edit_completeness(registry, step)
            if complete:
                return ok, last_out, deviations
            deviations.append(f"step {step.get('step_id')}: primary patch applied but incomplete: {msg}")
            used_fallback = True
        else:
            used_fallback = True
    elif new_s or step.get("change"):
        used_fallback = True

    if not used_fallback:
        return False, last_out, deviations

    ok_read, file_content = _read_file_snippet(registry, path)
    if not ok_read:
        return False, file_content, deviations

    ok, out = _llm_edit_fallback(registry, step, llm=llm, file_content=file_content)
    if ok:
        deviations.append(
            f"step {step.get('step_id')}: used LLM fallback edit (plan patch did not apply verbatim)"
        )
        complete, msg, errs = _validate_edit_completeness(registry, step)
        if complete:
            return ok, out, deviations
        ok_read, file_content = _read_file_snippet(registry, path)
        if not ok_read:
            return False, file_content, deviations
        ok_retry, out_retry = _llm_edit_fallback(
            registry,
            step,
            llm=llm,
            file_content=file_content,
            extra_context=f"Incomplete after first fallback: {msg}. Apply ALL planned lines.",
        )
        if ok_retry:
            deviations.append(f"step {step.get('step_id')}: required second LLM fallback attempt")
            complete, msg, errs = _validate_edit_completeness(registry, step)
            if complete:
                return True, out_retry, deviations
        return False, f"Edit incomplete after fallback: {msg}", deviations + errs

    return False, out, deviations


def _execute_step(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    llm: BaseChatModel,
) -> tuple[bool, str, list[str]]:
    action = step.get("action", "")
    path = _step_path(step)
    step_id = step.get("step_id", 0)
    _log_step(step_id, action, path)
    deviations: list[str] = []

    try:
        if action == "LOCATE":
            ok, out = _execute_locate(registry, step)
        elif action == "SEARCH":
            ok, out = _execute_search(registry, step)
        elif action == "VERIFY":
            ok, out = _execute_verify(registry, step, plan)
        elif action == "EDIT":
            ok, out, deviations = _execute_edit(registry, step, create_files=plan.create_files, llm=llm)
        else:
            ok, out = False, f"Unknown action: {action!r}"
    except Exception as e:
        _log_step_result(step_id, False, str(e))
        raise RuntimeError(
            f"Executor step {step_id} action={action!r} file={path!r} failed: {e}"
        ) from e

    _log_step_result(step_id, ok, out)
    return ok, out, deviations


def _assert_file_allowed(step: dict[str, Any], plan: ExecutionPlan) -> None:
    path = _step_path(step)
    if not path:
        return
    allowed = set(plan.files_to_change)
    if allowed and path not in allowed and not plan.create_files:
        raise RuntimeError(
            f"Step {step.get('step_id')} file {path!r} not in files_to_change "
            f"and create_files=false"
        )


@traceable(name="executor_agent")
def run_executor(
    plan: ExecutionPlan,
    repo_path: str,
    *,
    llm: BaseChatModel | None = None,
) -> ExecutionResult:
    """Execute all plan steps in order; stop on first failure."""
    configure_logging()
    _log.info("start", ticket_id=plan.ticket_id, repo_path=repo_path, steps=len(plan.execution_steps))

    registry, _ = build_executor_tools(repo_path)
    step_results: list[dict[str, Any]] = []
    changed_files: set[str] = set()
    plan_deviations: list[str] = []

    try:
        for raw_step in plan.execution_steps:
            step = _step_to_dict(raw_step)
            _assert_file_allowed(step, plan)
            ok, out, deviations = _execute_step(registry, step, plan, llm)
            plan_deviations.extend(deviations)
            step_results.append(
                {
                    "step_id": step.get("step_id"),
                    "action": step.get("action"),
                    "file": _step_path(step),
                    "ok": ok,
                    "output": out[:500],
                    "deviations": deviations,
                }
            )
            if not ok:
                raise RuntimeError(
                    f"Executor step {step.get('step_id')} action={step.get('action')!r} "
                    f"file={_step_path(step)!r} failed: {out}"
                )
            if step.get("action") == "EDIT":
                changed_files.add(_step_path(step))

        changed_files.update(plan.files_to_change)
        result = ExecutionResult(
            ticket_id=plan.ticket_id,
            changed_files=sorted(changed_files),
            steps_completed=len(step_results),
            step_results=step_results,
            plan_deviations=list(dict.fromkeys(plan_deviations)),
        )
        _log.info("complete", ticket_id=plan.ticket_id, steps=result.steps_completed)
        return result
    finally:
        registry.dispose()


def _save_failure(state: AgentState, plan: dict, error: str, now: str) -> None:
    from src.agents.planner.agent import plan_hash

    save_failed_attempt(
        {
            "attempt_id": state["session_id"],
            "ticket_id": state["jira_ticket_id"],
            "phase_failed": "IMPLEMENTATION",
            "error_type": "execution_error",
            "error_summary": error,
            "plan_hash": plan_hash(plan) if plan else None,
            "plan_used": plan,
            "timestamp": now,
        }
    )


@traceable(name="Executor Agent — Implementation")
def executor_node(state: AgentState) -> AgentState:
    """LangGraph node — runs plan steps in repo_path (git/MR deferred to Level 4c)."""
    now = datetime.now(timezone.utc).isoformat()
    plan_raw = state.get("plan_output")
    if not plan_raw:
        return {
            **state,
            "current_phase": "IMPLEMENTATION",
            "exit_message": "No approved plan in state. Cannot execute.",
            "updated_at": now,
        }

    try:
        plan = ExecutionPlan.model_validate(plan_raw)
    except ValidationError as e:
        return {
            **state,
            "current_phase": "IMPLEMENTATION",
            "exit_message": f"Executor input validation failed: {e}",
            "updated_at": now,
        }

    repo_path = state.get("repo_path") or "."
    try:
        result = run_executor(plan, repo_path)
    except Exception as e:
        _save_failure(state, plan_raw, str(e), now)
        return {
            **state,
            "current_phase": "IMPLEMENTATION",
            "current_error_log": str(e),
            "exit_message": f"Executor failed: {e}",
            "updated_at": now,
        }

    return {
        **state,
        "execution_output": ExecutionOutput(
            mr_url="",
            changed_files=result.changed_files,
            git_diff_summary="",
            worktree_path=repo_path,
            branch_name="",
            compilation_status="SKIPPED",
        ),
        "current_phase": "REVIEW",
        "prompt_version": "executor-v1.0",
        "updated_at": now,
    }
