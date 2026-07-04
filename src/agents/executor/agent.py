"""Executor Agent — deterministic orchestration with LLM semantic post-edit validation."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import traceable
from pydantic import BaseModel, ValidationError

from src.agents.executor.tools.builder import build_executor_tools
from src.agents.executor.validation import (
    PreEditCapture,
    ValidationResult,
    build_retry_note,
    capture_pre_edit,
    load_validation_policy,
    read_modified_snippet,
    validate_edit,
)
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
from src.core.semantic_target import ResolvedTarget, is_semantic_edit, resolve_semantic_target
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


def _resolved_target_excerpt(resolved: ResolvedTarget | None, max_lines: int = 30) -> str:
    """Trim resolved snippet for Cursor edit brief."""
    if not resolved:
        return "(target not resolved on disk)"
    lines = resolved.snippet.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + "\n..."
    return resolved.snippet


def _build_edit_instruction(
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    resolved: ResolvedTarget | None = None,
    retry_note: str = "",
) -> str:
    """Build semantic edit instruction for Cursor."""
    path = _step_path(step)
    parts = [
        f"Ticket: {plan.ticket_id}",
        f"File: {path}",
        f"Reason: {step.get('reason') or step.get('why', '')}",
    ]
    if is_semantic_edit(step):
        target_type = step.get("target_type") or "block"
        target_name = step.get("target_name") or ""
        approx = step.get("approximate_line")
        parent = step.get("parent_name")
        parts.append(
            f"Target: {target_type} `{target_name}`"
            + (f" (lines ~{approx})" if approx else "")
            + (f", parent: {parent}" if parent else "")
        )
        parts.append(f"Instruction: {step.get('instruction') or step.get('change') or step.get('target')}")
        if resolved:
            parts.append(
                f"Resolved context (lines {resolved.start_line}-{resolved.end_line} from disk):"
            )
            parts.append(_resolved_target_excerpt(resolved))
    else:
        parts.append(f"Change: {step.get('change') or step.get('target') or step.get('what', '')}")
        old_s = step.get("old_string") or ""
        new_s = step.get("new_string") or ""
        if old_s or new_s:
            parts.append("Action: REPLACE the following exact text:")
            parts.append(old_s)
            parts.append("WITH:")
            parts.append(new_s)
    if step.get("intent_marker"):
        parts.append(f"After edit, file MUST contain intent marker: {step['intent_marker']}")
    if retry_note:
        parts.append(retry_note)
    parts.append("Apply ONLY this change. Do not modify unrelated files. Preserve code style.")
    return "\n".join(parts)


def _semantic_edit_metadata(
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    resolved: ResolvedTarget | None = None,
) -> dict[str, Any]:
    """Metadata payload for gateway.edit — semantic fields, no patches."""
    meta: dict[str, Any] = {
        "step_id": step.get("step_id"),
        "ticket_id": plan.ticket_id,
        "intent_marker": step.get("intent_marker"),
        "instruction": step.get("instruction"),
        "target_type": step.get("target_type"),
        "target_name": step.get("target_name"),
        "approximate_line": step.get("approximate_line"),
        "parent_name": step.get("parent_name"),
        "end_line": step.get("end_line"),
        "create_file": bool(
            plan.create_files and step.get("target_type") == "file"
        ),
    }
    if resolved:
        meta["resolved_start_line"] = resolved.start_line
        meta["resolved_end_line"] = resolved.end_line
    return meta


def _apply_edit_via_cursor(
    gateway: CursorGateway,
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    repo_path: str,
    retry_note: str = "",
) -> tuple[bool, str]:
    """Apply semantic edit via CursorGateway."""
    path = _step_path(step)
    resolved = resolve_semantic_target(repo_path, step) if is_semantic_edit(step) else None
    instruction = _build_edit_instruction(step, plan, resolved=resolved, retry_note=retry_note)
    metadata = _semantic_edit_metadata(step, plan, resolved=resolved)
    result = gateway.edit(instruction, files=[path], metadata=metadata)
    return _cursor_edit_ok(result)


def _edit_steps(plan: ExecutionPlan) -> list[dict[str, Any]]:
    """Edit Steps for this module."""
    return [
        s
        for s in (_step_to_dict(raw) for raw in plan.execution_steps)
        if s.get("action") == "EDIT"
    ]


def _independent_edits(plan: ExecutionPlan) -> bool:
    """True when every file has at most one EDIT (no chained same-file edits).

    Chained edits depend on prior edits already being on disk, so they can only be
    applied sequentially — batching them into one call would break ordering.
    """
    counts: dict[str, int] = {}
    for step in _edit_steps(plan):
        path = _step_path(step)
        counts[path] = counts.get(path, 0) + 1
    return bool(counts) and all(count == 1 for count in counts.values())


def _build_batched_instruction(
    plan: ExecutionPlan,
    steps: list[dict[str, Any]],
    *,
    repo_path: str,
) -> str:
    """Build batched semantic edit instruction."""
    parts = [
        f"Apply ALL of the following edits in a single pass for ticket {plan.ticket_id} "
        f"(risk={plan.risk_level}).",
        "Do not stop after the first file — every edit below must be applied.",
        "",
    ]
    for step in steps:
        path = _step_path(step)
        resolved = resolve_semantic_target(repo_path, step) if is_semantic_edit(step) else None
        parts.append(f"--- [step {step.get('step_id')}] {path} ---")
        parts.append(_build_edit_instruction(step, plan, resolved=resolved))
        parts.append("")
    parts.append("Do not modify unrelated files. Preserve code style.")
    return "\n".join(parts)


def _apply_batched_edits(
    gateway: CursorGateway,
    plan: ExecutionPlan,
    *,
    repo_path: str,
) -> tuple[bool, str]:
    """Send every EDIT step to Cursor in one consolidated call."""
    steps = _edit_steps(plan)
    if not steps:
        return True, "no edits to batch"
    files = list(dict.fromkeys(_step_path(s) for s in steps if _step_path(s)))
    edits = []
    for step in steps:
        resolved = resolve_semantic_target(repo_path, step) if is_semantic_edit(step) else None
        meta = _semantic_edit_metadata(step, plan, resolved=resolved)
        edits.append(meta | {"file": _step_path(step)})
    instruction = _build_batched_instruction(plan, steps, repo_path=repo_path)
    metadata = {
        "ticket_id": plan.ticket_id,
        "create_files": plan.create_files,
        "edits": edits,
        "semantic": True,
    }
    _log.info("cursor_batched_edit", files=files, edit_count=len(edits))
    result = gateway.edit(instruction, files=files, metadata=metadata)
    return _cursor_edit_ok(result)


def _accept_batched_edit(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    llm: BaseChatModel,
    validation_policy: str,
    pre_edit: PreEditCapture,
) -> tuple[bool, str, list[str]]:
    """Validate that the batched Cursor call already applied this EDIT."""
    modified = read_modified_snippet(registry.cwd, step, pre_edit)
    result = validate_edit(
        llm,
        plan,
        step,
        original_snippet=pre_edit.original_snippet,
        modified_snippet=modified,
        validation_policy=validation_policy,
        attempt=1,
    )
    if result.passed:
        return True, "applied via single batched cursor call", []
    return False, result.reason, list(result.missing_requirements)


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


def _execute_read(registry: ToolRegistry, step: dict[str, Any]) -> tuple[bool, str]:
    """Execute Read for this module."""
    path = _step_path(step)
    start = step.get("read_start_line") or 1
    end = step.get("read_end_line")
    try:
        resolved = _resolve_repo_file(registry, path)
    except ValueError as e:
        return False, str(e)
    if not resolved.is_file():
        return False, f"file not found: {path}"
    result = registry.execute("read", path=path, start_line=start, end_line=end)
    return _tool_ok(result)


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

    if is_semantic_edit(step):
        intent_marker = (step.get("intent_marker") or "").strip()
        if intent_marker and intent_marker not in content:
            return False, f"intent marker not found: {intent_marker}"
        return True, "edit intent validated"

    planned_new = step.get("new_string") or ""
    planned_old = step.get("old_string") or ""
    if planned_old and planned_new and planned_old == planned_new:
        return False, "planned patch has no changes"

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
    intent_marker = (step.get("intent_marker") or "").strip() or _prior_edit_intent_marker(
        plan, path, step.get("step_id", 0)
    )
    if intent_marker:
        ok, out, code = verify_intent_marker(registry.cwd, path, intent_marker)
        if not ok:
            return False, out
        ok_read, content = _read_file_snippet(registry, path)
        if ok_read and intent_marker in content:
            _log.info("verification", mode="intent_marker", path=path, marker=intent_marker[:80])
            return True, f"intent marker verified: {intent_marker}"

    patterns, forbidden = _resolve_verify_patterns(step, plan)

    if not patterns and not forbidden:
        _log.info("verification", mode="file_exists", path=path)
        try:
            resolved = _resolve_repo_file(registry, path)
        except ValueError as e:
            return False, str(e)
        return resolved.is_file(), "" if resolved.is_file() else f"file not found: {path}"

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
    if not snapshots_match(planner_head, snap["head"]):
        raise RuntimeError(
            f"Repository snapshot mismatch: planner HEAD {planner_head[:12]} != "
            f"executor HEAD {(snap['head'] or '')[:12]} at {repo_path}. "
            f"status={snap['status_short']!r}"
        )


def _execute_edit(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    create_files: bool,
    llm: BaseChatModel,
    gateway: CursorGateway,
    ticket: dict[str, Any] | None = None,
    validation_policy: str = "",
    pre_edit: PreEditCapture | None = None,
    cursor_attempts_used: int = 0,
    skip_initial_cursor: bool = False,
) -> tuple[bool, str, list[str]]:
    """Execute EDIT via Cursor with LLM semantic validation and configurable retry budget."""
    path = _step_path(step)
    deviations: list[str] = []
    step_id = step.get("step_id", 0)
    repo_path = registry.cwd
    max_attempts = settings.cursor_max_edit_attempts
    policy = validation_policy or load_validation_policy(ticket)

    _log.info(
        "executor_edit_start",
        step_id=step_id,
        file=path,
        semantic=is_semantic_edit(step),
        cursor_attempts_used=cursor_attempts_used,
    )

    if pre_edit is None:
        pre_edit = capture_pre_edit(repo_path, step)

    ok_read, read_err = _read_file_snippet(registry, path)
    if not ok_read and not (create_files and step.get("target_type") == "file"):
        return False, read_err, deviations

    last_out = ""
    prior_failure: ValidationResult | None = None
    retry_note = ""

    for attempt_num in range(cursor_attempts_used + 1, max_attempts + 1):
        should_call_cursor = not (
            skip_initial_cursor and attempt_num == cursor_attempts_used + 1
        )

        if should_call_cursor:
            ok, last_out = _apply_edit_via_cursor(
                gateway, step, plan, repo_path=repo_path, retry_note=retry_note
            )
            post_hash = file_content_hash(repo_path, path)
            _log.info(
                "executor_post_edit_file",
                step_id=step_id,
                file=path,
                file_sha256=post_hash,
                edit_ok=ok,
                cursor_attempt=attempt_num,
            )
            if not ok:
                is_noop = "No changes made" in last_out or "identical content" in last_out
                if not is_noop:
                    return False, last_out, deviations

        modified_snippet = read_modified_snippet(repo_path, step, pre_edit)
        validation_attempt = attempt_num - cursor_attempts_used
        result = validate_edit(
            llm,
            plan,
            step,
            original_snippet=pre_edit.original_snippet,
            modified_snippet=modified_snippet,
            validation_policy=policy,
            prior_failure=prior_failure,
            attempt=validation_attempt,
        )

        if result.passed:
            if attempt_num > cursor_attempts_used + 1:
                deviations.append(f"step {step_id}: semantic edit required one retry")
            return True, last_out or result.reason, deviations

        if attempt_num < max_attempts:
            _log.info(
                "executor_validation_retry",
                ticket_id=plan.ticket_id,
                step_id=step_id,
                file=path,
                cursor_attempt=attempt_num + 1,
                prior_reason=result.reason,
            )
            prior_failure = result
            retry_note = build_retry_note(result)
        else:
            return False, f"Edit incomplete: {result.reason}", list(result.missing_requirements)

    return False, "Edit incomplete: max cursor attempts exhausted", deviations


def _execute_step(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    llm: BaseChatModel,
    gateway: CursorGateway,
    ticket: dict[str, Any] | None = None,
    *,
    batched: bool = False,
    validation_policy: str = "",
    pre_edit_by_step: dict[int, PreEditCapture] | None = None,
) -> tuple[bool, str, list[str]]:
    """Execute Step for this module."""
    action = step.get("action", "")
    path = _step_path(step)
    step_id = step.get("step_id", 0)
    _log_step(step_id, action, path)
    deviations: list[str] = []
    policy = validation_policy or load_validation_policy(ticket)
    pre_edit_map = pre_edit_by_step or {}

    try:
        if action == "READ":
            ok, out = _execute_read(registry, step)
        elif action == "VERIFY":
            ok, out = _execute_verify(registry, step, plan)
        elif action == "EDIT":
            if batched:
                pre_edit = pre_edit_map.get(step_id) or capture_pre_edit(registry.cwd, step)
                ok, out, deviations = _accept_batched_edit(
                    registry,
                    step,
                    plan,
                    llm=llm,
                    validation_policy=policy,
                    pre_edit=pre_edit,
                )
                if not ok:
                    ok, out, repair_devs = _execute_edit(
                        registry,
                        step,
                        plan,
                        create_files=plan.create_files,
                        llm=llm,
                        gateway=gateway,
                        ticket=ticket,
                        validation_policy=policy,
                        pre_edit=pre_edit,
                        cursor_attempts_used=1,
                        skip_initial_cursor=True,
                    )
                    deviations = deviations + repair_devs + [
                        f"step {step_id}: batched edit needed per-file repair"
                    ]
            else:
                ok, out, deviations = _execute_edit(
                    registry,
                    step,
                    plan,
                    create_files=plan.create_files,
                    llm=llm,
                    gateway=gateway,
                    ticket=ticket,
                    validation_policy=policy,
                )
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
    return ok, out, deviations


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
) -> ExecutionResult:
    """Execute all plan steps in order; stop on first failure."""
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

    # Single-call contract: when edits are independent (one EDIT per file), send them all
    # to Cursor in ONE consolidated call up front. Chained same-file edits fall back to the
    # sequential per-step path because they depend on prior edits already being on disk.
    batched = _independent_edits(plan)
    pre_edit_by_step: dict[int, PreEditCapture] = {}
    if batched:
        for edit_step in _edit_steps(plan):
            sid = edit_step.get("step_id", 0)
            pre_edit_by_step[sid] = capture_pre_edit(repo_path, edit_step)
        b_ok, b_msg = _apply_batched_edits(gateway, plan, repo_path=repo_path)
        if b_ok:
            plan_deviations.append("cursor: all edits applied in a single batched call")
        else:
            plan_deviations.append(
                f"cursor: batched edit incomplete ({b_msg}); repairing failed files per-step"
            )

    try:
        for raw_step in plan.execution_steps:
            step = _step_to_dict(raw_step)
            _assert_file_allowed(step, plan)
            ok, out, deviations = _execute_step(
                registry,
                step,
                plan,
                llm,
                gateway,
                ticket=ticket,
                batched=batched,
                validation_policy=validation_policy,
                pre_edit_by_step=pre_edit_by_step,
            )
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

    return {
        **state,
        "execution_output": ExecutionOutput(
            mr_url=mr_url,
            changed_files=result.changed_files,
            git_diff_summary=diff_summary[:5000],
            worktree_path=worktree_path,
            branch_name=branch_name,
            compilation_status="SKIPPED",
        ),
        "current_phase": "REVIEW",
        "prompt_version": "executor-v1.0",
        "updated_at": now,
    }
