"""Executor Agent — deterministic orchestration with LLM semantic post-edit validation."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import traceable
from pydantic import BaseModel, Field, ValidationError

from src.agents.executor.tools.builder import build_executor_tools
from src.agents.executor.structural_check import check_file_structure, is_python_hard_fail
from src.agents.executor.validation import (
    EditStepOutcome,
    FailedEditStep,
    PreEditCapture,
    ValidationResult,
    build_retry_note,
    capture_pre_edit,
    load_validation_policy,
    log_edit_outcome,
    noop_failure_result,
    read_modified_snippet,
    structural_failure_result,
    validate_edit,
    validate_edit_progressive,
)
from src.core.edit_step import is_instruction_edit
from src.core.llm import TaskComplexity, get_llm
from src.core.logging import configure_logging, get_logger
from src.core.file_paths import repair_repo_from_cursor_temp_clone, remap_cursor_path
from src.core.repo_snapshot import (
    file_content_hash,
    git_repo_snapshot,
    log_repo_snapshot,
    snapshots_match,
)
from src.core.skill_scripts.run_tests import run_tests_for_file
from src.core.skill_scripts.verify_edit import verify_intent_marker
from src.core.plan_validation import sync_files_to_change_from_edit_steps
from src.core.schemas import ExecutionPlan, ToolResult
from src.core.state import AgentState, ExecutionOutput
from src.core.config import settings
from src.integrations.cursor.gateway import (
    CursorGateway,
    LocalPatchCursorGateway,
    get_cursor_gateway,
)
from src.integrations.cursor.schemas import CursorEditResult
from src.memory.cache import save_failed_attempt
from src.tools.path_utils import resolve_path_sandbox
from src.tools.registry import ToolRegistry
from src.core.token_usage import reset_tracing_agent, set_tracing_agent

_log = get_logger("executor")

_LEGACY_ACTION_MAP = {"LOCATE": "READ", "SEARCH": "READ"}


class ExecutionResult(BaseModel):
    """Executor node output — changed files, per-step tool results, plan deviations.
    Returned from `run_executor`; attached to PipelineResult.execution."""
    ticket_id: str
    changed_files: list[str]
    steps_completed: int
    step_results: list[dict[str, Any]]
    plan_deviations: list[str] = []
    validation_failed_steps: list[int] = Field(default_factory=list)
    validation_warning: str = ""


def _load_skills(ticket: dict[str, Any] | None = None) -> str:
    """Load Skills for this module."""
    return load_validation_policy(ticket)


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
    """Step Path for this module."""
    return step.get("file") or step.get("where") or ""


def _normalize_executor_plan(plan: ExecutionPlan, repo_path: str) -> ExecutionPlan:
    """Remap Cursor temp_clone paths and materialize missing checkout files."""
    repair_repo_from_cursor_temp_clone(repo_path)
    for step in plan.execution_steps:
        step.file = remap_cursor_path(step.file, repo_path)
    if plan.files_to_change:
        plan.files_to_change = list(
            dict.fromkeys(remap_cursor_path(f, repo_path) for f in plan.files_to_change)
        )
    return sync_files_to_change_from_edit_steps(plan)


def _step_to_dict(step: Any) -> dict[str, Any]:
    """Step To Dict for this module."""
    if hasattr(step, "model_dump"):
        d = step.model_dump(by_alias=True)
    else:
        d = dict(step)
    action = d.get("action", "")
    if action in _LEGACY_ACTION_MAP:
        d["action"] = _LEGACY_ACTION_MAP[action]
    return d


def _tool_ok(result: ToolResult) -> tuple[bool, str]:
    """Tool Ok for this module."""
    return not result.is_error, result.output


def _cursor_edit_ok(result: CursorEditResult) -> tuple[bool, str]:
    """Cursor Edit Ok for this module."""
    if result.dry_run:
        return True, result.text or "dry_run edit (no file mutation)"
    if result.exit_code != 0 or result.status in ("failed", "error"):
        msg = result.stderr or result.text or f"Cursor edit failed: {result.status}"
        return False, msg
    if not result.changed_files and result.status not in ("dry_run", "success", "finished", "completed"):
        return False, result.text or "Cursor edit returned no changed files"
    return True, result.text or "edit applied via CursorGateway"


def _build_edit_instruction(
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    retry_note: str = "",
) -> str:
    """Build instruction-based edit brief for Cursor — planner instruction is verbatim."""
    path = _step_path(step)
    instruction = (step.get("instruction") or step.get("change") or step.get("target") or "").strip()
    parts = [
        f"Ticket: {plan.ticket_id}",
        f"File: {path}",
        f"Reason: {step.get('reason') or step.get('why', '')}",
        "",
        "## Instruction (from planner — includes acceptance criteria)",
        instruction,
        "",
    ]
    if step.get("approximate_line"):
        parts.append(f"Approximate location: line ~{step['approximate_line']}")
    for hint in ("target_type", "target_name", "parent_name"):
        if step.get(hint):
            parts.append(f"Semantic target — {hint}: {step[hint]}")
    if step.get("caution_flag"):
        parts.extend([
            "",
            "## Ambiguity caution (from planner — NOT an error)",
            step["caution_flag"],
            "The file path is correct. target_type/target_name may match multiple locations.",
            "Use the instruction and security context to fix the correct occurrence.",
            "Do not treat ambiguity as a failure condition.",
        ])
    marker = (step.get("intent_marker") or "").strip()
    if marker:
        parts.append(f"Mechanical constraint: after edit, file MUST contain intent_marker: {marker}")
    parts.append("Mechanical constraint: modify ONLY this file. Preserve code style.")
    if retry_note:
        parts.append(retry_note)
    return "\n".join(parts)


def _semantic_edit_metadata(
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> dict[str, Any]:
    """Metadata payload for gateway.edit — instruction fields, no patches."""
    return {
        "step_id": step.get("step_id"),
        "ticket_id": plan.ticket_id,
        "intent_marker": step.get("intent_marker"),
        "instruction": step.get("instruction"),
        "target_type": step.get("target_type"),
        "target_name": step.get("target_name"),
        "approximate_line": step.get("approximate_line"),
        "parent_name": step.get("parent_name"),
        "caution_flag": step.get("caution_flag"),
        "end_line": step.get("end_line"),
        "create_file": bool(plan.create_files and step.get("target_type") == "file"),
    }


def _apply_batched_edits(
    gateway: CursorGateway,
    plan: ExecutionPlan,
    steps: list[dict[str, Any]],
    *,
    retry_notes: dict[int, str] | None = None,
    batch_pass: int = 1,
) -> tuple[bool, str]:
    """Send the given EDIT steps to Cursor in one consolidated call."""
    if not steps:
        return True, "no edits to batch"
    notes = retry_notes or {}
    files = list(dict.fromkeys(_step_path(s) for s in steps if _step_path(s)))
    edits = []
    for step in steps:
        meta = _semantic_edit_metadata(step, plan)
        edits.append(meta | {"file": _step_path(step)})
    instruction = _build_batched_instruction(plan, steps, retry_notes=notes)
    metadata = {
        "ticket_id": plan.ticket_id,
        "create_files": plan.create_files,
        "edits": edits,
        "semantic": True,
        "batch_pass": batch_pass,
    }
    _log.info(
        "cursor_batched_edit",
        files=files,
        edit_count=len(edits),
        batch_pass=batch_pass,
    )
    result = gateway.edit(instruction, files=files, metadata=metadata)
    return _cursor_edit_ok(result)


def _build_batched_instruction(
    plan: ExecutionPlan,
    steps: list[dict[str, Any]],
    *,
    retry_notes: dict[int, str] | None = None,
) -> str:
    """Build batched instruction-based edit brief."""
    notes = retry_notes or {}
    parts = [
        f"Apply ALL of the following edits in a single pass for ticket {plan.ticket_id} "
        f"(risk={plan.risk_level}).",
        "Do not stop after the first file — every edit below must be applied.",
        "",
    ]
    for step in steps:
        path = _step_path(step)
        sid = step.get("step_id", 0)
        parts.append(f"--- [step {sid}] {path} ---")
        parts.append(_build_edit_instruction(step, plan, retry_note=notes.get(sid, "")))
        parts.append("")
    parts.append("Do not modify unrelated files. Preserve code style.")
    return "\n".join(parts)


def _edit_steps(plan: ExecutionPlan) -> list[dict[str, Any]]:
    """Edit Steps for this module."""
    return [
        s
        for s in (_step_to_dict(raw) for raw in plan.execution_steps)
        if s.get("action") == "EDIT"
    ]


def _format_aggregate_edit_failure(failures: list[FailedEditStep]) -> str:
    """Build a RuntimeError message listing every step still failing after batch passes."""
    lines = [
        f"Executor edit validation failed after {settings.cursor_max_edit_attempts} "
        f"batch pass(es). {len(failures)} step(s) still failing:"
    ]
    for failure in failures:
        lines.append(f"  - step {failure.step_id} ({failure.file}): {failure.reason}")
        if failure.missing_requirements:
            lines.append(f"    missing: {failure.missing_requirements}")
    return "\n".join(lines)


def _should_continue_after_validation_failure(
    *,
    continue_on_validation_failure: bool,
    repair_attempted: bool,
) -> bool:
    """Continue execution (and allow PR creation) after validation failures.

    Batch pass 1 failures alone still raise — pass 2 repair must run first.
    After pass 2, soft validation keeps partial Cursor edits and opens a PR.
    """
    if continue_on_validation_failure:
        return True
    return settings.semantic_soft_validation_enabled and repair_attempted


def _outcome_from_result(
    step: dict[str, Any],
    result: ValidationResult,
    *,
    required_repair: bool = False,
    output: str = "",
) -> EditStepOutcome:
    """Wrap a ValidationResult as an EditStepOutcome."""
    step_id = step.get("step_id", 0)
    path = _step_path(step)
    deviations: list[str] = []
    if required_repair:
        deviations.append(f"step {step_id}: edit required batch pass 2 repair")
    msg = output or (result.reason if result.passed else f"Edit incomplete: {result.reason}")
    return EditStepOutcome(
        step_id=step_id,
        step=step,
        file=path,
        passed=result.passed,
        result=result,
        output=msg,
        deviations=deviations,
        required_repair=required_repair,
    )


def _failed_from_result(step: dict[str, Any], result: ValidationResult) -> FailedEditStep:
    """Wrap a failed ValidationResult as a FailedEditStep."""
    step_id = step.get("step_id", 0)
    return FailedEditStep(
        step_id=step_id,
        file=_step_path(step),
        step=step,
        reason=result.reason,
        missing_requirements=list(result.missing_requirements),
        retry_note=build_retry_note(result),
        result=result,
    )


def _log_step(step_id: int, action: str, file_path: str) -> None:
    """Log Step for this module."""
    _log.info("step", step=step_id, action=action, file=file_path)


def _log_step_result(step_id: int, ok: bool, output: str) -> None:
    """Log Step Result for this module."""
    _log.info("result", step=step_id, status="ok" if ok else "failed", output=output[:500])


def _resolve_repo_file(registry: ToolRegistry, path: str) -> Path:
    """Resolve Repo File for this module."""
    return resolve_path_sandbox(path, registry.cwd)


def _is_inert_comment_line(line: str) -> bool:
    """True for comment-only lines that must not trigger forbidden-pattern failures."""
    stripped = line.lstrip()
    return (
        stripped.startswith("#")
        or stripped.startswith("//")
        or stripped.startswith("*")
        or stripped.startswith("<!--")
    )


def _file_contains_fixed(content: str, pattern: str) -> bool:
    """Fixed-string substring match (same semantics as grep -F on a single file)."""
    if not pattern:
        return False
    return any(pattern in line for line in content.splitlines())


def _file_contains_live_pattern(content: str, pattern: str) -> bool:
    """Forbidden-pattern check — ignore comment-only lines (e.g. redacted PEM placeholders)."""
    if not pattern:
        return False
    for line in content.splitlines():
        if _is_inert_comment_line(line):
            continue
        if pattern in line:
            return True
    return False


_READ_DELEGATED_MSG = "delegated to cursor batch context (pre-edit capture used for validation)"


def _execute_read_delegated(step: dict[str, Any]) -> tuple[bool, str]:
    """READ steps are Cursor batch context — Python does not replay them."""
    path = _step_path(step)
    _log.info("read_delegated", step=step.get("step_id"), file=path)
    return True, _READ_DELEGATED_MSG


_REMOVAL_HINTS = (
    "delete",
    "remove",
    "redact",
    "gitignore",
    "exclude",
    "purge",
    "strip",
    "placeholder",
    "no longer contain",
    "do not commit",
)


def _edit_step_blob(step: dict[str, Any]) -> str:
    return " ".join(
        [
            step.get("instruction") or "",
            step.get("change") or "",
            step.get("reason") or "",
            step.get("target") or "",
        ]
    ).lower()


def _removal_expected(plan: ExecutionPlan, path: str) -> bool:
    """True when any EDIT on *path* intends to delete or redact file content."""
    for raw in plan.execution_steps:
        step = _step_to_dict(raw)
        if step.get("action") != "EDIT" or _step_path(step) != path:
            continue
        if any(hint in _edit_step_blob(step) for hint in _REMOVAL_HINTS):
            return True
    return False


def _preflight_edit_targets(plan: ExecutionPlan, repo_path: str) -> None:
    """Fail fast when EDIT targets are missing on disk (phantom paths)."""
    root = Path(repo_path)
    missing: list[str] = []
    for raw in plan.execution_steps:
        step = _step_to_dict(raw)
        if step.get("action") != "EDIT":
            continue
        path = _step_path(step)
        if not path:
            continue
        creates = bool(plan.create_files and step.get("target_type") == "file")
        if creates:
            continue
        if not (root / path).is_file():
            missing.append(path)
    if missing:
        raise RuntimeError(
            "Preflight failed: EDIT target(s) not found on disk: "
            + ", ".join(missing)
        )


def _execute_run_tests(registry: ToolRegistry, step: dict[str, Any]) -> tuple[bool, str]:
    """Execute Run Tests for this module."""
    path = _step_path(step)
    ok, out, code = run_tests_for_file(registry.cwd, path)
    if code == 2:
        return True, out
    return ok, out


def _is_human_verify_text(text: str) -> bool:
    """Is Human Verify Text for this module."""
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
        raw = match.group(1).strip()
        if raw.startswith("`") and "`" in raw[1:]:
            token = raw[1 : raw.index("`", 1)]
        elif raw.startswith("`"):
            token = raw.strip("`")
        else:
            token = raw.strip("\"'`")
        if not token or _is_human_verify_text(token):
            continue
        if token.startswith("!"):
            forbidden.append(token[1:].strip())
        else:
            required.append(token)
    return required, forbidden


def _edit_targets_sql(step: dict[str, Any]) -> bool:
    """True when the planned edit is about SQL/query parameterization."""
    blob = " ".join(
        [
            step.get("instruction") or "",
            step.get("change") or "",
            step.get("reason") or "",
            step.get("intent_marker") or "",
            step.get("old_string") or "",
            step.get("new_string") or "",
        ]
    ).lower()
    markers = (
        "execute(",
        "cur.execute",
        "cursor.execute",
        " sql",
        "select ",
        "insert ",
        "update ",
        "delete ",
        "like ?",
        "parameteriz",
        "%s",
        "values (%s)",
    )
    return any(marker in blob for marker in markers)


def _read_file_snippet(registry: ToolRegistry, path: str, *, max_lines: int = 200) -> tuple[bool, str]:
    """Read File Snippet for this module."""
    try:
        resolved = _resolve_repo_file(registry, path)
        text = resolved.read_text(encoding="utf-8")
    except (ValueError, OSError) as e:
        return False, str(e)
    lines = text.splitlines()
    if max_lines and len(lines) > max_lines:
        text = "\n".join(lines[:max_lines])
    return True, text


def _patterns_from_prior_edits(plan: ExecutionPlan, path: str, step_id: int) -> list[str]:
    """Use intent markers from the latest prior EDIT on the same file."""
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
    marker = (latest.get("intent_marker") or "").strip()
    return [marker] if marker else []


def _validate_edit_intent(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> tuple[bool, str]:
    """Validate fix intent via intent_marker, security heuristics, and VERIFY patterns."""
    path = _step_path(step)
    ok_read, content = _read_file_snippet(registry, path)
    if not ok_read:
        return False, content

    if is_instruction_edit(step):
        intent_marker = (step.get("intent_marker") or "").strip()
        if intent_marker and intent_marker not in content:
            return False, f"intent marker not found: {intent_marker}"
        return True, "edit intent validated"

    errors: list[str] = []

    if _edit_targets_sql(step):
        if re.search(r'f["\'].*SELECT.*\{query\}', content):
            errors.append("vulnerable f-string SQL interpolation still present")
        if "LIKE ?" not in content and "?" not in content:
            errors.append("parameterized query marker not found")

    intent_marker = step.get("intent_marker")
    if intent_marker and intent_marker not in content:
        errors.append(f"intent marker not found: {intent_marker}")

    validate = step.get("validate") or ""
    for raw in plan.execution_steps:
        candidate = _step_to_dict(raw)
        if candidate.get("action") == "VERIFY" and _step_path(candidate) == path:
            validate = validate or candidate.get("validate") or ""
            break

    required, forbidden = _extract_grep_patterns_from_validate(validate)
    for pattern in required:
        if not _file_contains_fixed(content, pattern):
            errors.append(f"required pattern {pattern!r} not found")
    for pattern in forbidden:
        if _file_contains_live_pattern(content, pattern):
            errors.append(f"vulnerable pattern {pattern!r} still present")

    if errors:
        return False, "; ".join(errors)
    return True, "edit intent validated"


def _forbidden_patterns_from_prior_edits(plan: ExecutionPlan, path: str, step_id: int) -> list[str]:
    """Forbidden tokens derived from prior EDIT validate/forbidden hints."""
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
    _, forbidden = _extract_grep_patterns_from_validate(latest.get("validate") or "")
    return forbidden


def _resolve_verify_patterns(
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> tuple[list[str], list[str]]:
    """Build required/forbidden grep patterns for VERIFY."""
    path = _step_path(step)
    step_id = step.get("step_id", 0)
    validate = step.get("validate") or step.get("step_validate") or ""
    target = step.get("target") or step.get("what") or ""

    edit_patterns = _patterns_from_prior_edits(plan, path, step_id)
    validate_required, validate_forbidden = _extract_grep_patterns_from_validate(validate)

    if edit_patterns:
        # Prior EDIT new_string is the source of truth — ignore planner validate required patterns.
        required = list(edit_patterns)
    else:
        required = list(validate_required)
        if target and not _is_human_verify_text(target):
            if any(c in target for c in ("=", "%", "(", '"', "'", ".", "/")):
                required.append(target.strip())

    forbidden = list(
        dict.fromkeys(
            validate_forbidden
            + _forbidden_patterns_from_prior_edits(plan, path, step_id)
        )
    )
    required = list(dict.fromkeys(p for p in required if p))
    return required, forbidden


def _prior_edit_intent_marker(plan: ExecutionPlan, path: str, step_id: int) -> str | None:
    """Intent marker from the latest prior EDIT on the same file."""
    latest: str | None = None
    latest_id = -1
    for raw in plan.execution_steps:
        prior = _step_to_dict(raw)
        if prior.get("action") != "EDIT":
            continue
        if _step_path(prior) != path:
            continue
        prior_id = prior.get("step_id", 0)
        if prior_id >= step_id or prior_id <= latest_id:
            continue
        marker = (prior.get("intent_marker") or "").strip()
        if marker:
            latest = marker
            latest_id = prior_id
    return latest


def _execute_verify(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> tuple[bool, str]:
    """Execute Verify for this module."""
    path = _step_path(step)
    try:
        resolved = _resolve_repo_file(registry, path)
        file_exists = resolved.is_file()
    except ValueError as e:
        return False, str(e)

    if not file_exists and _removal_expected(plan, path):
        _log.info("verification", mode="removal_expected", path=path)
        return True, "file removed as expected"

    intent_marker = (step.get("intent_marker") or "").strip() or _prior_edit_intent_marker(
        plan, path, step.get("step_id", 0)
    )
    if intent_marker:
        if not file_exists:
            return False, f"file not found: {path}"
        ok, out, code = verify_intent_marker(registry.cwd, path, intent_marker)
        if not ok:
            return False, out
        _log.info("verification", mode="intent_marker", path=path, marker=intent_marker[:80])
        return True, f"intent marker verified: {intent_marker}"

    patterns, forbidden = _resolve_verify_patterns(step, plan)

    if not patterns and not forbidden:
        _log.info("verification", mode="deferred_to_edit_validation", path=path)
        return True, "deferred to EDIT LLM validation"

    if not file_exists:
        if forbidden and not patterns:
            _log.info("verification", mode="forbidden_absent_file", path=path)
            return True, "forbidden patterns absent (file removed)"
        return False, f"file not found: {path}"

    ok_read, content = _read_file_snippet(registry, path)
    if not ok_read:
        return False, content

    errors: list[str] = []
    outputs: list[str] = []
    for pattern in patterns:
        _log.info("verification", mode="contains", pattern=pattern[:120], path=path)
        if _file_contains_fixed(content, pattern):
            outputs.append(pattern)
        else:
            errors.append(f"required pattern {pattern!r} not found")

    for pattern in forbidden:
        _log.info("verification", mode="forbidden", pattern=pattern[:120], path=path)
        if _file_contains_live_pattern(content, pattern):
            errors.append(f"vulnerable pattern {pattern!r} still present")

    if errors:
        return False, "; ".join(errors)
    return True, outputs[0] if outputs else "verification passed"


def _assert_snapshot_consistency(plan: ExecutionPlan, repo_path: str) -> None:
    """Block execution when executor checkout diverges from planner snapshot."""
    planner_head = plan.planner_snapshot_head
    if not planner_head:
        return
    snap = git_repo_snapshot(repo_path)
    if not snap["is_git"]:
        raise RuntimeError(
            f"Executor worktree is not a git repository at {repo_path}. "
            f"Planner used HEAD {planner_head[:12]}. "
            "The worktree directory may be corrupt (missing .git) — remove it and retry."
        )
    if not snapshots_match(planner_head, snap["head"]):
        raise RuntimeError(
            f"Repository snapshot mismatch: planner HEAD {planner_head[:12]} != "
            f"executor HEAD {(snap['head'] or '')[:12]} at {repo_path}. "
            f"status={snap['status_short']!r}"
        )


def _read_full_file(registry: ToolRegistry, path: str) -> tuple[bool, str]:
    """Read entire file content for structural checks."""
    ok, content = _read_file_snippet(registry, path)
    return ok, content


def _evaluate_post_edit(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    llm: BaseChatModel,
    pre_edit: PreEditCapture,
    policy: str,
    prior_failure: ValidationResult | None,
    validation_attempt: int,
    pre_hash: str,
    post_hash: str,
    cursor_called: bool,
) -> ValidationResult:
    """Run noop, structural, then progressive LLM validation after a Cursor edit."""
    path = _step_path(step)
    repo_path = registry.cwd

    if cursor_called and pre_hash == post_hash:
        from src.agents.executor.validation import _validation_snippets
        from src.agents.executor.validation_context import ContextLevel

        original_snippet, modified_snippet, _, _ = _validation_snippets(
            repo_path, step, pre_edit, level=ContextLevel.DIFF_SEMANTIC
        )
        noop_check = validate_edit(
            llm,
            plan,
            step,
            original_snippet=original_snippet,
            modified_snippet=modified_snippet,
            validation_policy=policy,
            prior_failure=prior_failure,
            attempt=validation_attempt,
        )
        if noop_check.passed:
            return noop_check
        return noop_failure_result()

    structural_warnings: list[str] = []
    intent_marker_in_file: bool | None = None
    ok_read, content = _read_full_file(registry, path)
    if ok_read:
        marker = (step.get("intent_marker") or "").strip()
        if marker:
            intent_marker_in_file = marker in content
        structural_errors = check_file_structure(path, content)
        if structural_errors:
            if is_python_hard_fail(path):
                return structural_failure_result(structural_errors)
            structural_warnings = structural_errors

    return validate_edit_progressive(
        llm,
        plan,
        step,
        repo_path=repo_path,
        pre_edit=pre_edit,
        validation_policy=policy,
        prior_failure=prior_failure,
        structural_warnings=structural_warnings or None,
        intent_marker_in_file=intent_marker_in_file,
        attempt=validation_attempt,
    )


def _validate_all_edits(
    registry: ToolRegistry,
    plan: ExecutionPlan,
    steps: list[dict[str, Any]],
    *,
    llm: BaseChatModel,
    validation_policy: str,
    pre_edit_by_step: dict[int, PreEditCapture],
    pre_hash_by_step: dict[int, str],
    batch_pass: int = 1,
    required_repair_step_ids: set[int] | None = None,
) -> tuple[list[EditStepOutcome], list[FailedEditStep]]:
    """Validate each EDIT step after a batch Cursor call."""
    passed: list[EditStepOutcome] = []
    failed: list[FailedEditStep] = []
    repair_ids = required_repair_step_ids or set()

    for step in steps:
        step_id = step.get("step_id", 0)
        path = _step_path(step)
        pre_edit = pre_edit_by_step.get(step_id)
        if pre_edit is None:
            pre_edit = capture_pre_edit(registry.cwd, step)
        pre_hash = pre_hash_by_step.get(step_id, file_content_hash(registry.cwd, path))
        post_hash = file_content_hash(registry.cwd, path)

        result = _evaluate_post_edit(
            registry,
            step,
            plan,
            llm=llm,
            pre_edit=pre_edit,
            policy=validation_policy,
            prior_failure=None,
            validation_attempt=batch_pass,
            pre_hash=pre_hash,
            post_hash=post_hash,
            cursor_called=True,
        )

        log_edit_outcome(
            ticket_id=plan.ticket_id,
            step_id=step_id,
            file_path=path,
            retry_count=batch_pass - 1,
            result=result,
            noop_diff=pre_hash == post_hash and not result.passed,
        )

        required_repair = step_id in repair_ids and result.passed
        if result.passed:
            output = (
                "applied via batched cursor call (pass 2 repair)"
                if required_repair
                else "applied via batched cursor call"
            )
            passed.append(
                _outcome_from_result(
                    step,
                    result,
                    required_repair=required_repair,
                    output=output,
                )
            )
        else:
            failed.append(_failed_from_result(step, result))

    return passed, failed


def _run_edit_batch_phase(
    registry: ToolRegistry,
    plan: ExecutionPlan,
    *,
    gateway: CursorGateway,
    llm: BaseChatModel,
    validation_policy: str,
    repo_path: str,
    continue_on_validation_failure: bool = False,
) -> tuple[dict[int, EditStepOutcome], list[str], list[FailedEditStep]]:
    """Apply all EDIT steps in up to two batch Cursor calls; validate between passes."""
    edit_steps = _edit_steps(plan)
    edit_outcomes: dict[int, EditStepOutcome] = {}
    plan_deviations: list[str] = []
    if not edit_steps:
        return edit_outcomes, plan_deviations, []

    max_passes = settings.cursor_max_edit_attempts
    pre_edit_by_step: dict[int, PreEditCapture] = {}
    pre_hash_by_step: dict[int, str] = {}

    for step in edit_steps:
        sid = step.get("step_id", 0)
        pre_edit_by_step[sid] = capture_pre_edit(repo_path, step)
        pre_hash_by_step[sid] = file_content_hash(repo_path, _step_path(step))

    b_ok, b_msg = _apply_batched_edits(gateway, plan, edit_steps, batch_pass=1)
    if b_ok:
        plan_deviations.append("cursor: batch pass 1 applied all edits")
    else:
        plan_deviations.append(f"cursor: batch pass 1 incomplete ({b_msg})")

    passed, failed = _validate_all_edits(
        registry,
        plan,
        edit_steps,
        llm=llm,
        validation_policy=validation_policy,
        pre_edit_by_step=pre_edit_by_step,
        pre_hash_by_step=pre_hash_by_step,
        batch_pass=1,
    )
    for outcome in passed:
        edit_outcomes[outcome.step_id] = outcome

    still_failed = failed
    repair_attempted = False
    if failed and max_passes >= 2:
        repair_attempted = True
        retry_notes = {f.step_id: f.retry_note for f in failed}
        failed_step_ids = {f.step_id for f in failed}
        for failure in failed:
            sid = failure.step_id
            pre_edit_by_step[sid] = capture_pre_edit(repo_path, failure.step)
            pre_hash_by_step[sid] = file_content_hash(repo_path, failure.file)

        repair_steps = [f.step for f in failed]
        r_ok, r_msg = _apply_batched_edits(
            gateway,
            plan,
            repair_steps,
            retry_notes=retry_notes,
            batch_pass=2,
        )
        if r_ok:
            plan_deviations.append("cursor: batch pass 2 repair applied for failed steps")
        else:
            plan_deviations.append(f"cursor: batch pass 2 incomplete ({r_msg})")

        passed2, still_failed = _validate_all_edits(
            registry,
            plan,
            repair_steps,
            llm=llm,
            validation_policy=validation_policy,
            pre_edit_by_step=pre_edit_by_step,
            pre_hash_by_step=pre_hash_by_step,
            batch_pass=2,
            required_repair_step_ids=failed_step_ids,
        )
        for outcome in passed2:
            edit_outcomes[outcome.step_id] = outcome

    if still_failed:
        if _should_continue_after_validation_failure(
            continue_on_validation_failure=continue_on_validation_failure,
            repair_attempted=repair_attempted,
        ):
            for failure in still_failed:
                edit_outcomes[failure.step_id] = _outcome_from_result(
                    failure.step,
                    failure.result,
                    output=f"Edit incomplete: {failure.reason}",
                )
            plan_deviations.append(
                f"edit validation: {len(still_failed)} step(s) failed after batch passes"
            )
            if repair_attempted:
                plan_deviations.append(
                    "edit validation: soft-fail after batch pass 2 — PR will include partial edits"
                )
            return edit_outcomes, plan_deviations, still_failed
        raise RuntimeError(_format_aggregate_edit_failure(still_failed))

    return edit_outcomes, plan_deviations, []


def _execute_step(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    llm: BaseChatModel,
    gateway: CursorGateway,
    ticket: dict[str, Any] | None = None,
    *,
    edit_outcomes: dict[int, EditStepOutcome] | None = None,
) -> tuple[bool, str, list[str], dict[str, Any] | None]:
    """Execute Step for this module."""
    del llm, gateway, ticket
    action = step.get("action", "")
    path = _step_path(step)
    step_id = step.get("step_id", 0)
    _log_step(step_id, action, path)
    deviations: list[str] = []
    edit_outcome: dict[str, Any] | None = None
    outcomes = edit_outcomes or {}

    try:
        if action == "READ":
            ok, out = _execute_read_delegated(step)
        elif action == "VERIFY":
            ok, out = _execute_verify(registry, step, plan)
        elif action == "EDIT":
            outcome = outcomes.get(step_id)
            if outcome is None:
                ok, out = False, f"No edit outcome recorded for step {step_id}"
            else:
                ok = outcome.passed
                out = outcome.output
                deviations = list(outcome.deviations)
                edit_outcome = outcome.to_telemetry_dict(plan.ticket_id)
        elif action == "RUN_TESTS":
            ok, out = _execute_run_tests(registry, step)
        else:
            ok, out = False, f"Unknown action: {action!r}"
    except Exception as e:
        _log_step_result(step_id, False, str(e))
        raise RuntimeError(
            f"Executor step {step_id} action={action!r} file={path!r} failed: {e}"
        ) from e

    _log_step_result(step_id, ok, out)
    return ok, out, deviations, edit_outcome


def _assert_file_allowed(step: dict[str, Any], plan: ExecutionPlan) -> None:
    """Assert File Allowed for this module."""
    path = _step_path(step)
    if not path:
        return
    action = step.get("action", "")
    # Planner emits READ→VERIFY on files outside files_to_change for verify-only
    # mitigations (e.g. hypothesis fixed elsewhere or pending follow-up patch).
    if action in ("READ", "VERIFY"):
        return
    allowed = set(plan.files_to_change)
    if allowed and path not in allowed and not plan.create_files:
        raise RuntimeError(
            f"Step {step.get('step_id')} file {path!r} not in files_to_change "
            f"and create_files=false"
        )


def _resolve_executor_llm(llm: BaseChatModel | None) -> BaseChatModel:
    """Return injected LLM, env stub for subprocess tests, or default LOW-tier model."""
    if llm is not None:
        return llm
    stub = os.getenv("EXECUTOR_VALIDATION_STUB_RESPONSE")
    if stub:

        class _StubLLM:
            def invoke(self, _prompt: Any) -> Any:
                class _Resp:
                    content = stub

                return _Resp()

        return _StubLLM()  # type: ignore[return-value]
    return get_llm(TaskComplexity.LOW)


@traceable(name="executor_agent")
def run_executor(
    plan: ExecutionPlan,
    repo_path: str,
    *,
    llm: BaseChatModel | None = None,
    gateway: CursorGateway | None = None,
    dry_run: bool = False,
    ticket: dict[str, Any] | None = None,
    continue_on_edit_validation_failure: bool = False,
) -> ExecutionResult:
    """Execute all plan steps in order; stop on first failure unless continue mode."""
    configure_logging()
    plan = _normalize_executor_plan(plan, repo_path)
    log_repo_snapshot(_log, "start", repo_path, ticket_id=plan.ticket_id, steps=len(plan.execution_steps))
    _assert_snapshot_consistency(plan, repo_path)

    tracing_token = set_tracing_agent("executor")
    llm = _resolve_executor_llm(llm)
    validation_policy = load_validation_policy(ticket)
    if gateway is None:
        if dry_run:
            gateway = get_cursor_gateway(Path(repo_path), dry_run=True)
        elif settings.cursor_api_key:
            gateway = get_cursor_gateway(Path(repo_path), dry_run=False)
        else:
            _log.warning("cursor_api_key_missing", fallback="LocalPatchCursorGateway")
            gateway = LocalPatchCursorGateway(repo_path)

    registry, _ = build_executor_tools(repo_path)
    step_results: list[dict[str, Any]] = []
    changed_files: set[str] = set()
    plan_deviations: list[str] = []

    try:
        _preflight_edit_targets(plan, repo_path)
        edit_outcomes, edit_deviations, validation_failures = _run_edit_batch_phase(
            registry,
            plan,
            gateway=gateway,
            llm=llm,
            validation_policy=validation_policy,
            repo_path=repo_path,
            continue_on_validation_failure=continue_on_edit_validation_failure,
        )
        plan_deviations.extend(edit_deviations)
        validation_failed_steps = [f.step_id for f in validation_failures]
        soft_fail_mode = bool(validation_failures)
        validation_warning = (
            _format_aggregate_edit_failure(validation_failures) if validation_failures else ""
        )

        for raw_step in plan.execution_steps:
            step = _step_to_dict(raw_step)
            _assert_file_allowed(step, plan)
            ok, out, deviations, edit_outcome = _execute_step(
                registry,
                step,
                plan,
                llm,
                gateway,
                ticket=ticket,
                edit_outcomes=edit_outcomes,
            )
            plan_deviations.extend(deviations)
            step_entry: dict[str, Any] = {
                "step_id": step.get("step_id"),
                "action": step.get("action"),
                "file": _step_path(step),
                "ok": ok,
                "output": out[:500],
                "deviations": deviations,
            }
            if edit_outcome:
                step_entry["edit_outcome"] = edit_outcome
            step_results.append(step_entry)
            if not ok:
                if soft_fail_mode:
                    if step.get("action") == "EDIT":
                        changed_files.add(_step_path(step))
                    continue
                raise RuntimeError(
                    f"Executor step {step.get('step_id')} action={step.get('action')!r} "
                    f"file={_step_path(step)!r} failed: {out}"
                )
            if step.get("action") == "EDIT":
                changed_files.add(_step_path(step))

        if soft_fail_mode:
            for failure in validation_failures:
                changed_files.add(failure.file)

        changed_files.update(plan.files_to_change)
        result = ExecutionResult(
            ticket_id=plan.ticket_id,
            changed_files=sorted(changed_files),
            steps_completed=len(step_results),
            step_results=step_results,
            plan_deviations=list(dict.fromkeys(plan_deviations)),
            validation_failed_steps=validation_failed_steps,
            validation_warning=validation_warning,
        )
        _log.info("complete", ticket_id=plan.ticket_id, steps=result.steps_completed)
        return result
    finally:
        registry.dispose()
        reset_tracing_agent(tracing_token)


def _save_failure(state: AgentState, plan: dict, error: str, now: str) -> None:
    """Save Failure for this module."""
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
    """LangGraph node — worktree-isolated execution and GitHub PR creation."""
    from src.agents.executor.tools.git_worktree import create_worktree, show_diff, stage_commit_push
    from src.agents.executor.tools.mr_create import create_github_pr

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
    ticket_id = state.get("jira_ticket_id") or plan.ticket_id
    try:
        worktree_info = create_worktree(
            repo_path,
            ticket_id,
            base_branch=state.get("git_target_branch"),
            description=plan.root_hypothesis[:30] if plan.root_hypothesis else "",
            planner_snapshot_head=plan.planner_snapshot_head,
        )
        worktree_path = worktree_info["worktree_path"]
        branch_name = worktree_info["branch_name"]
        _log.info(
            "worktree_ready",
            worktree_path=worktree_path,
            branch_name=branch_name,
            status=worktree_info.get("status"),
        )

        result = run_executor(
            plan,
            worktree_path,
            gateway=get_cursor_gateway(Path(worktree_path), dry_run=False),
            ticket=state.get("jira_ticket"),
        )
        diff_summary = show_diff(worktree_path, worktree_info.get("base_branch"))
        commit_msg = f"fix({ticket_id}): {plan.root_hypothesis[:72]}"
        push_result = stage_commit_push(worktree_path, branch_name, commit_msg)
        _log.info("git_push", **push_result, branch=branch_name)

        if not push_result.get("pushed") and not push_result.get("branch_on_remote"):
            raise RuntimeError(
                f"Cannot open PR: {push_result.get('message', 'branch not pushed')}. "
                f"Branch {branch_name!r} is not on origin."
            )

        mr_url = create_github_pr(
            worktree_path,
            branch_name,
            ticket_id=ticket_id,
            title=f"[{ticket_id}] {plan.root_hypothesis[:80]}",
            body=f"Automated fix for Jira ticket {ticket_id}.\n\n{plan.root_hypothesis}",
            base_branch=worktree_info.get("base_branch"),
            ticket=state.get("jira_ticket"),
        )
    except Exception as e:
        _save_failure(state, plan_raw, str(e), now)
        return {
            **state,
            "current_phase": "IMPLEMENTATION",
            "current_error_log": str(e),
            "exit_message": f"Executor failed: {e}",
            "updated_at": now,
        }

    execution_output: ExecutionOutput = {
        "mr_url": mr_url,
        "changed_files": result.changed_files,
        "git_diff_summary": diff_summary[:5000],
        "worktree_path": worktree_path,
        "branch_name": branch_name,
        "compilation_status": "SKIPPED",
    }
    if result.validation_failed_steps:
        execution_output["validation_failed_steps"] = result.validation_failed_steps
        execution_output["validation_warning"] = result.validation_warning

    return {
        **state,
        "execution_output": execution_output,
        "current_phase": "REVIEW",
        "exit_message": (
            f"Executor completed with validation warnings on step(s) "
            f"{result.validation_failed_steps}. PR created for review."
            if result.validation_failed_steps
            else None
        ),
        "prompt_version": "executor-v1.0",
        "updated_at": now,
    }
