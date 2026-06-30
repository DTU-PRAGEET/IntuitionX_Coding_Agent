"""Executor Agent — deterministic-first step runner with LOW-tier LLM fallback on EDIT."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import traceable
from pydantic import BaseModel, ValidationError

from src.agents.executor.tools.builder import build_executor_tools
from src.core.llm import TaskComplexity, get_llm
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import invoke_tracked, llm_content
from src.core.prompt_builder import PromptBuilder
from src.core.skill_registry import format_domain_skills_for_prompt, module_skills_dir
from src.core.repo_snapshot import (
    file_content_hash,
    file_git_diff,
    git_repo_snapshot,
    log_edit_patch,
    log_repo_snapshot,
    old_string_in_file,
    sha256_text,
    snapshots_match,
)
from src.core.skill_scripts.run_tests import run_tests_for_file
from src.core.skill_scripts.verify_edit import verify_intent_marker
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

_log = get_logger("executor")

SKILLS_DIR = module_skills_dir("executor")
_LEGACY_ACTION_MAP = {"LOCATE": "READ", "SEARCH": "READ"}


class ExecutionResult(BaseModel):
    ticket_id: str
    changed_files: list[str]
    steps_completed: int
    step_results: list[dict[str, Any]]
    plan_deviations: list[str] = []


def _load_skills(ticket: dict[str, Any] | None = None) -> str:
    body, _ = PromptBuilder.build_system_only(SKILLS_DIR)
    domain = format_domain_skills_for_prompt("executor", ticket)
    if domain:
        return f"{body}\n\n{domain}"
    return body


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
    return step.get("file") or step.get("where") or ""


def _step_to_dict(step: Any) -> dict[str, Any]:
    if hasattr(step, "model_dump"):
        d = step.model_dump(by_alias=True)
    else:
        d = dict(step)
    action = d.get("action", "")
    if action in _LEGACY_ACTION_MAP:
        d["action"] = _LEGACY_ACTION_MAP[action]
    return d


def _tool_ok(result: ToolResult) -> tuple[bool, str]:
    return not result.is_error, result.output


def _cursor_edit_ok(result: CursorEditResult) -> tuple[bool, str]:
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
    create_file: bool = False,
    old_string: str | None = None,
    new_string: str | None = None,
) -> str:
    path = _step_path(step)
    old_s = old_string if old_string is not None else (step.get("old_string") or "")
    new_s = new_string if new_string is not None else (step.get("new_string") or "")
    parts = [
        f"Ticket: {plan.ticket_id}",
        f"File: {path}",
        f"Reason: {step.get('reason') or step.get('why', '')}",
        f"Change: {step.get('change') or step.get('target') or step.get('what', '')}",
    ]
    if step.get("intent_marker"):
        parts.append(f"After edit, file MUST contain intent marker: {step['intent_marker']}")
    if create_file and not old_s and new_s:
        parts.append("Action: CREATE new file with the following content:")
        parts.append(new_s)
    else:
        parts.append("Action: REPLACE the following exact text:")
        parts.append(old_s)
        parts.append("WITH:")
        parts.append(new_s)
    parts.append("Apply ONLY this change. Do not modify unrelated files. Preserve code style.")
    return "\n".join(parts)


def _apply_edit_via_cursor(
    gateway: CursorGateway,
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    create_files: bool,
    old_string: str | None = None,
    new_string: str | None = None,
) -> tuple[bool, str]:
    path = _step_path(step)
    old_s = old_string if old_string is not None else (step.get("old_string") or "")
    new_s = new_string if new_string is not None else (step.get("new_string") or "")
    create_file = create_files and not old_s and bool(new_s)
    instruction = _build_edit_instruction(
        step,
        plan,
        create_file=create_file,
        old_string=old_s,
        new_string=new_s,
    )
    metadata = {
        "step_id": step.get("step_id"),
        "ticket_id": plan.ticket_id,
        "old_string": old_s,
        "new_string": new_s,
        "create_file": create_file,
        "intent_marker": step.get("intent_marker"),
    }
    result = gateway.edit(instruction, files=[path], metadata=metadata)
    return _cursor_edit_ok(result)


def _edit_steps(plan: ExecutionPlan) -> list[dict[str, Any]]:
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


def _build_batched_instruction(plan: ExecutionPlan, steps: list[dict[str, Any]]) -> str:
    parts = [
        f"Apply ALL of the following edits in a single pass for ticket {plan.ticket_id} "
        f"(risk={plan.risk_level}).",
        "Do not stop after the first file — every edit below must be applied.",
        "",
    ]
    for step in steps:
        path = _step_path(step)
        old_s = step.get("old_string") or ""
        new_s = step.get("new_string") or ""
        parts.append(f"--- [step {step.get('step_id')}] {path} ---")
        if step.get("reason"):
            parts.append(f"Reason: {step['reason']}")
        if step.get("intent_marker"):
            parts.append(f"After edit, file MUST contain: {step['intent_marker']}")
        if plan.create_files and not old_s and new_s:
            parts.append("CREATE new file with content:")
            parts.append(new_s)
        else:
            parts.append("REPLACE the following exact text:")
            parts.append(old_s)
            parts.append("WITH:")
            parts.append(new_s)
        parts.append("")
    parts.append("Do not modify unrelated files. Preserve code style.")
    return "\n".join(parts)


def _apply_batched_edits(gateway: CursorGateway, plan: ExecutionPlan) -> tuple[bool, str]:
    """Send every EDIT step to Cursor in one consolidated call (single-call contract)."""
    steps = _edit_steps(plan)
    if not steps:
        return True, "no edits to batch"
    files = list(dict.fromkeys(_step_path(s) for s in steps if _step_path(s)))
    edits = []
    for step in steps:
        old_s = step.get("old_string") or ""
        new_s = step.get("new_string") or ""
        edits.append(
            {
                "step_id": step.get("step_id"),
                "file": _step_path(step),
                "old_string": old_s,
                "new_string": new_s,
                "create_file": bool(plan.create_files and not old_s and new_s),
                "intent_marker": step.get("intent_marker"),
            }
        )
    instruction = _build_batched_instruction(plan, steps)
    metadata = {
        "ticket_id": plan.ticket_id,
        "create_files": plan.create_files,
        "edits": edits,
    }
    _log.info("cursor_batched_edit", files=files, edit_count=len(edits))
    result = gateway.edit(instruction, files=files, metadata=metadata)
    return _cursor_edit_ok(result)


def _accept_batched_edit(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> tuple[bool, str, list[str]]:
    """Validate that the single batched Cursor call already applied this EDIT — no extra call."""
    old_s = step.get("old_string")
    new_s = step.get("new_string")
    if old_s is not None and new_s is not None and old_s == new_s:
        return False, (
            f"EDIT step {step.get('step_id')}: old_string identical to new_string"
        ), []
    # Whole-file replacement: if the file now hashes to the planned new_string, the batched
    # edit applied exactly — accept before line-level completeness (which over-rejects when
    # unchanged lines are shared between old_string and new_string).
    path = _step_path(step)
    if new_s:
        post_hash = file_content_hash(registry.cwd, path)
        if post_hash and post_hash == sha256_text(new_s):
            return True, "batched cursor edit applied (sha match)", []
    complete, msg, errs = _validate_edit_completeness(registry, step)
    if complete:
        return True, "applied via single batched cursor call", []
    deviations: list[str] = []
    accepted = _try_accept_by_intent(
        registry,
        step,
        plan,
        deviations,
        reason="batched cursor edit satisfied fix intent",
    )
    if accepted:
        return accepted
    return False, msg, errs


def _log_step(step_id: int, action: str, file_path: str) -> None:
    _log.info("step", step=step_id, action=action, file=file_path)


def _log_step_result(step_id: int, ok: bool, output: str) -> None:
    _log.info("result", step=step_id, status="ok" if ok else "failed", output=output[:500])


def _resolve_repo_file(registry: ToolRegistry, path: str) -> Path:
    return resolve_path_sandbox(path, registry.cwd)


def _file_contains_fixed(content: str, pattern: str) -> bool:
    """Fixed-string substring match (same semantics as grep -F on a single file)."""
    if not pattern:
        return False
    return any(pattern in line for line in content.splitlines())


def _execute_read(registry: ToolRegistry, step: dict[str, Any]) -> tuple[bool, str]:
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
    path = _step_path(step)
    ok, out, code = run_tests_for_file(registry.cwd, path)
    if code == 2:
        return True, out
    return ok, out


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


def _meaningful_patch_lines(text: str) -> list[str]:
    """Non-comment, non-trivial lines from a patch block."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 4 or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def _distinct_patch_lines(planned_old: str, planned_new: str) -> list[str]:
    """Lines introduced by the patch (in new_string but not old_string)."""
    old_lines = set(_meaningful_patch_lines(planned_old))
    return [ln for ln in _meaningful_patch_lines(planned_new) if ln not in old_lines]


def _edit_targets_sql(step: dict[str, Any]) -> bool:
    """True when the planned edit is about SQL/query parameterization."""
    blob = " ".join(
        [
            step.get("old_string") or "",
            step.get("new_string") or "",
            step.get("change") or "",
            step.get("reason") or "",
            step.get("intent_marker") or "",
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

    new_lines = set(_meaningful_patch_lines(planned_new))
    missing = [ln for ln in new_lines if ln not in content]
    # Shared context lines appear in both old and new — only flag lines removed by the patch.
    remaining = [
        ln
        for ln in _meaningful_patch_lines(planned_old)
        if ln in content and ln not in new_lines
    ]
    errors: list[str] = []
    if missing:
        errors.append(f"planned new_string lines missing: {missing}")
    if remaining:
        errors.append(f"planned old_string lines still present: {remaining}")
    if errors:
        return False, "; ".join(errors), errors
    return True, "edit matches plan", []


def _validate_edit_intent(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> tuple[bool, str]:
    """After LLM fallback: validate fix intent via security heuristics and VERIFY patterns."""
    path = _step_path(step)
    ok_read, content = _read_file_snippet(registry, path)
    if not ok_read:
        return False, content

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
        critical_lines = [
            ln
            for ln in _meaningful_patch_lines(planned_new)
            if any(marker in ln for marker in ("execute(sql", "params", "LIKE ?"))
        ]
        missing_critical = [ln for ln in critical_lines if ln not in content]
        if missing_critical:
            errors.append(f"critical planned fix lines missing: {missing_critical}")

    distinct_new = _distinct_patch_lines(planned_old, planned_new)
    missing_distinct = [ln for ln in distinct_new if ln not in content]
    if missing_distinct:
        errors.append(f"distinct planned fix lines missing: {missing_distinct}")

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
    if required or forbidden:
        verify_step = {
            "action": "VERIFY",
            "file": path,
            "validate": validate,
            "step_id": step.get("step_id"),
        }
        ok, msg = _execute_verify(registry, verify_step, plan)
        if not ok:
            errors.append(msg)
    elif (
        _edit_targets_sql(step)
        and "LIKE ?" in content
        and not re.search(r'f["\'].*SELECT.*\{query\}', content)
    ):
        # File already satisfies core SQLi fix — skip malformed planner validate prose.
        pass

    if errors:
        return False, "; ".join(errors)
    return True, "edit intent validated"


def _validate_edit_outcome(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    *,
    used_fallback: bool,
) -> tuple[bool, str, list[str]]:
    complete, msg, errs = _validate_edit_completeness(registry, step)
    if complete:
        return complete, msg, errs
    if not used_fallback:
        return complete, msg, errs
    ok, intent_msg = _validate_edit_intent(registry, step, plan)
    if ok:
        return True, f"fallback intent validated: {intent_msg}", [
            "patch deviation: validated by intent, not exact planner patch match"
        ]
    return False, f"{msg}; intent check failed: {intent_msg}", errs


def _llm_edit_fallback(
    registry: ToolRegistry,
    step: dict[str, Any],
    *,
    plan: ExecutionPlan,
    llm: BaseChatModel,
    file_content: str,
    gateway: CursorGateway,
    extra_context: str = "",
    ticket: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    path = _step_path(step)
    fallback_llm = llm or get_llm(TaskComplexity.LOW)
    skills = _load_skills(ticket)
    prompt = f"""{skills}

## EDIT FALLBACK
File: {path}
Change required: {step.get('change') or step.get('reason') or step.get('why', '')}
Planned replacement (apply ALL of these lines — not a subset):
{step.get('new_string') or '(see change description)'}
Previous patch error: old_string missing, failed, incomplete, or not applied
{extra_context}

## FILE CONTENT
{file_content}

Return ONLY JSON: {{"old_string": "...", "new_string": "..."}}
Apply the FULL intended transformation from the plan — include every line from the planned replacement."""

    try:
        response = invoke_tracked(fallback_llm, prompt, agent="executor")
        content = llm_content(response)
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return False, "LLM did not return valid edit JSON"
        patch = json.loads(json_match.group())
        old_fb = patch.get("old_string") or ""
        new_fb = patch.get("new_string") or ""
        _log.info(
            "llm_edit_fallback",
            path=path,
            raw_fallback_json=json_match.group(),
            old_len=len(old_fb),
            new_len=len(new_fb),
            old_eq_new=old_fb == new_fb,
        )
        if old_fb == new_fb:
            accepted = _try_accept_by_intent(
                registry,
                step,
                plan,
                [],
                reason="fallback identical patch but fix intent already satisfied",
            )
            if accepted:
                ok_intent, msg_intent, _devs = accepted
                return ok_intent, msg_intent
            return False, (
                f"LLM fallback rejected: identical old_string and new_string for {path}"
            )
        if not old_fb or not new_fb:
            return False, f"LLM fallback rejected: empty patch for {path}"
        return _apply_edit_via_cursor(
            gateway,
            step,
            plan,
            create_files=False,
            old_string=old_fb,
            new_string=new_fb,
        )
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
    old_lines = _meaningful_patch_lines(latest.get("old_string") or "")
    new_lines = set(_meaningful_patch_lines(latest.get("new_string") or ""))
    return [ln for ln in old_lines if ln not in new_lines]


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


def _execute_verify(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
) -> tuple[bool, str]:
    path = _step_path(step)
    intent_marker = step.get("intent_marker")
    if intent_marker:
        ok, out, code = verify_intent_marker(registry.cwd, path, intent_marker)
        if not ok:
            return False, out

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
        if _file_contains_fixed(content, pattern):
            errors.append(f"vulnerable pattern {pattern!r} still present")

    if errors:
        return False, "; ".join(errors)
    return True, outputs[0] if outputs else "verification passed"


def _try_accept_by_intent(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    deviations: list[str],
    *,
    reason: str,
) -> tuple[bool, str, list[str]] | None:
    """Return success tuple when file already satisfies fix intent despite patch mismatch."""
    ok, intent_msg = _validate_edit_intent(registry, step, plan)
    if not ok:
        return None
    deviations.append(f"step {step.get('step_id')}: {reason}")
    return True, intent_msg, deviations


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
) -> tuple[bool, str, list[str]]:
    path = _step_path(step)
    old_s = step.get("old_string")
    new_s = step.get("new_string")
    deviations: list[str] = []
    step_id = step.get("step_id", 0)

    log_edit_patch(
        _log,
        phase="executor_pre",
        step_id=step_id,
        file_path=path,
        old_string=old_s,
        new_string=new_s,
    )

    if not old_s and new_s and create_files:
        ok, out = _apply_edit_via_cursor(
            gateway, step, plan, create_files=True, old_string="", new_string=new_s
        )
        if ok:
            complete, msg, errs = _validate_edit_completeness(registry, step)
            if not complete:
                return False, msg, errs
            return ok, out, deviations

    # Mandatory fresh read before EDIT (Read → Edit → Verify triad)
    ok_read, file_content = _read_file_snippet(registry, path)
    if not ok_read:
        return False, file_content, deviations

    pre_check = old_string_in_file(registry.cwd, path, old_s or "")
    pre_hash = pre_check["file_sha256"]
    _log.info(
        "executor_pre_edit_file",
        step_id=step_id,
        file=path,
        file_sha256=pre_hash,
        old_string_exists=pre_check["exists"],
        occurrence_count=pre_check["occurrence_count"],
    )

    if old_s and new_s and old_s == new_s:
        _log.warning("executor_identical_patch", step_id=step_id, file=path)
        accepted = _try_accept_by_intent(
            registry,
            step,
            plan,
            deviations,
            reason="identical old/new patch but fix intent already satisfied",
        )
        if accepted:
            return accepted
        return False, (
            f"EDIT step {step_id}: old_string identical to new_string — edit tool not invoked"
        ), deviations

    used_fallback = False
    last_out = "EDIT missing patches"
    if old_s and new_s:
        if old_s not in file_content:
            deviations.append(
                f"step {step.get('step_id')}: plan old_string not in fresh read; attempting edit anyway"
            )
        result = _apply_edit_via_cursor(gateway, step, plan, create_files=create_files)
        ok, last_out = result
        post_hash = file_content_hash(registry.cwd, path)
        post_diff = file_git_diff(registry.cwd, path)
        _log.info(
            "executor_post_edit_file",
            step_id=step_id,
            file=path,
            file_sha256=post_hash,
            edit_ok=ok,
            file_diff=post_diff[:2000],
        )
        if ok:
            expected_sha = sha256_text(new_s) if new_s else ""
            sha_match = bool(expected_sha and post_hash == expected_sha)
            sha_delta = bool(pre_hash and post_hash and pre_hash != post_hash)
            if sha_match:
                _log.info(
                    "executor_sha_match",
                    step_id=step_id,
                    file=path,
                    sha=post_hash,
                )
                return ok, last_out, deviations
            if sha_delta:
                _log.info(
                    "executor_edit_confirmed_by_sha_delta",
                    step_id=step_id,
                    file=path,
                    pre_hash=pre_hash,
                    post_hash=post_hash,
                )
                return ok, last_out, deviations

            complete, msg, errs = _validate_edit_outcome(
                registry, step, plan, used_fallback=False
            )
            if complete:
                return ok, last_out, deviations
            accepted = _try_accept_by_intent(
                registry,
                step,
                plan,
                deviations,
                reason="primary patch incomplete but fix intent satisfied",
            )
            if accepted:
                return accepted
            deviations.append(f"step {step.get('step_id')}: primary patch applied but incomplete: {msg}")
            used_fallback = True
        else:
            if "No changes made" in last_out or "identical content" in last_out:
                accepted = _try_accept_by_intent(
                    registry,
                    step,
                    plan,
                    deviations,
                    reason="primary edit no-op but fix intent satisfied",
                )
                if accepted:
                    return accepted
            used_fallback = True
    elif new_s or step.get("change"):
        used_fallback = True

    if not used_fallback:
        return False, last_out, deviations

    ok_read, file_content = _read_file_snippet(registry, path)
    if not ok_read:
        return False, file_content, deviations

    ok, out = _llm_edit_fallback(
        registry, step, plan=plan, llm=llm, file_content=file_content, gateway=gateway, ticket=ticket
    )
    if ok:
        deviations.append(
            f"step {step.get('step_id')}: used LLM fallback edit (plan patch did not apply verbatim)"
        )
        complete, msg, errs = _validate_edit_outcome(
            registry, step, plan, used_fallback=True
        )
        if complete:
            return ok, out, deviations
        ok_read, file_content = _read_file_snippet(registry, path)
        if not ok_read:
            return False, file_content, deviations
        ok_retry, out_retry = _llm_edit_fallback(
            registry,
            step,
            plan=plan,
            llm=llm,
            file_content=file_content,
            gateway=gateway,
            extra_context=f"Incomplete after first fallback: {msg}. Apply ALL planned lines.",
            ticket=ticket,
        )
        if ok_retry:
            deviations.append(f"step {step.get('step_id')}: required second LLM fallback attempt")
            complete, msg, errs = _validate_edit_outcome(
                registry, step, plan, used_fallback=True
            )
            if complete:
                return True, out_retry, deviations
        return False, f"Edit incomplete after fallback: {msg}", deviations + errs

    if "No changes made" in out or "identical content" in out:
        accepted = _try_accept_by_intent(
            registry,
            step,
            plan,
            deviations,
            reason="fallback no-op but fix intent satisfied",
        )
        if accepted:
            return accepted

    return False, out, deviations


def _execute_step(
    registry: ToolRegistry,
    step: dict[str, Any],
    plan: ExecutionPlan,
    llm: BaseChatModel,
    gateway: CursorGateway,
    ticket: dict[str, Any] | None = None,
    *,
    batched: bool = False,
) -> tuple[bool, str, list[str]]:
    action = step.get("action", "")
    path = _step_path(step)
    step_id = step.get("step_id", 0)
    _log_step(step_id, action, path)
    deviations: list[str] = []

    try:
        if action == "READ":
            ok, out = _execute_read(registry, step)
        elif action == "VERIFY":
            ok, out = _execute_verify(registry, step, plan)
        elif action == "EDIT":
            if batched:
                # Edits were already sent in one consolidated Cursor call — validate only.
                ok, out, deviations = _accept_batched_edit(registry, step, plan)
                if not ok:
                    # Per-file repair (the single permitted retry for a failed edit).
                    ok, out, repair_devs = _execute_edit(
                        registry,
                        step,
                        plan,
                        create_files=plan.create_files,
                        llm=llm,
                        gateway=gateway,
                        ticket=ticket,
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
    log_repo_snapshot(_log, "start", repo_path, ticket_id=plan.ticket_id, steps=len(plan.execution_steps))
    _assert_snapshot_consistency(plan, repo_path)

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
    if batched:
        b_ok, b_msg = _apply_batched_edits(gateway, plan)
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
                registry, step, plan, llm, gateway, ticket=ticket, batched=batched
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
