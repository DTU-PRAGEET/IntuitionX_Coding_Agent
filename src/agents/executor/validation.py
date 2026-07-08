"""LLM semantic validation for executor post-edit checks."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from src.core.edit_step import is_instruction_edit
from src.core.logging import get_logger
from src.core.runner_utils import invoke_tracked, llm_content, parse_llm_json
from src.core.schemas import ExecutionPlan
from src.core.skill_manager import PromptBuilder, format_domain_skills_for_prompt, module_skills_dir

from src.agents.executor.validation_context import (
    ContextLevel,
    _read_file_lines,
    warrants_context_expansion,
)

_log = get_logger("executor.validation")

SKILLS_DIR = module_skills_dir("executor")
DEFAULT_PADDING = 3
DEFAULT_SPAN_LINES = 40


class ValidationResult(BaseModel):
    """Outcome of a single semantic edit validation."""

    passed: bool
    confidence: float
    reason: str
    missing_requirements: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    noop_diff: bool = False
    structural_fail: bool = False


class SemanticValidationResponse(BaseModel):
    """Strict JSON schema returned by the validation LLM."""

    model_config = ConfigDict(populate_by_name=True)

    passed: bool = Field(alias="pass")
    confidence: float
    reason: str
    missing_requirements: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PreEditCapture:
    """Original snippet and line span captured before Cursor mutates the file."""

    original_snippet: str
    start_line: int
    end_line: int
    full_file_content: str = ""


@dataclass
class EditStepOutcome:
    """Result of validating a single EDIT step after a batch pass."""

    step_id: int
    step: dict[str, Any]
    file: str
    passed: bool
    result: ValidationResult
    output: str
    deviations: list[str]
    required_repair: bool = False

    def to_telemetry_dict(self, ticket_id: str) -> dict[str, Any]:
        """Structured edit_outcome payload for step_results."""
        return {
            "ticket_id": ticket_id,
            "step_id": self.step_id,
            "file": self.file,
            "retry_count": 1 if self.required_repair else 0,
            "structural_fail": self.result.structural_fail,
            "noop_diff": self.result.noop_diff,
            "validation_fail_reason": self.result.reason if not self.passed else None,
            "missing_requirements": list(self.result.missing_requirements)
            if not self.passed
            else [],
            "passed": self.passed,
        }


@dataclass
class FailedEditStep:
    """An EDIT step that failed semantic/structural validation."""

    step_id: int
    file: str
    step: dict[str, Any]
    reason: str
    missing_requirements: list[str]
    retry_note: str
    result: ValidationResult


def load_validation_policy(ticket: dict[str, Any] | None = None) -> str:
    """Load executor skill body as validation policy."""
    body, _ = PromptBuilder.build_system_only(SKILLS_DIR)
    domain = format_domain_skills_for_prompt("executor", ticket)
    if domain:
        return f"{body}\n\n{domain}"
    return body


def read_snippet_at_span(
    repo_path: str,
    file_path: str,
    start_line: int,
    end_line: int,
    *,
    padding: int = DEFAULT_PADDING,
) -> str:
    """Read a file region by line span with optional surrounding context."""
    full = Path(repo_path) / file_path
    if not full.is_file():
        return ""
    try:
        lines = full.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    lo = max(1, start_line - padding)
    hi = min(len(lines), end_line + padding)
    return "\n".join(lines[lo - 1 : hi])


def _read_file_lines(repo_path: str, file_path: str) -> list[str]:
    full = Path(repo_path) / file_path
    if not full.is_file():
        return []
    try:
        return full.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def capture_pre_edit(
    repo_path: str,
    step: dict[str, Any],
) -> PreEditCapture:
    """Capture original snippet and line span before any Cursor edit."""
    from src.agents.executor.validation_context import pre_edit_span

    path = step.get("file") or step.get("where") or ""
    full_path = Path(repo_path) / path
    full_content = ""
    lines: list[str] = []
    if full_path.is_file():
        try:
            full_content = full_path.read_text(encoding="utf-8")
            lines = full_content.splitlines()
        except OSError:
            full_content = ""
    start_line, end_line = pre_edit_span(step, lines, path)
    snippet = read_snippet_at_span(repo_path, path, start_line, end_line, padding=0)
    return PreEditCapture(
        original_snippet=snippet,
        start_line=start_line,
        end_line=end_line,
        full_file_content=full_content,
    )


def _validation_snippets(
    repo_path: str,
    step: dict[str, Any],
    pre_edit: PreEditCapture,
    *,
    level: Any | None = None,
) -> tuple[str, str, int, int]:
    """Resolve original/modified snippets using progressive context selection."""
    from src.agents.executor.validation_context import ContextLevel, resolve_validation_snippets

    ctx_level = level or ContextLevel.DIFF_SEMANTIC
    original_snippet, modified_snippet, start, end, _ = resolve_validation_snippets(
        repo_path,
        step,
        pre_edit,
        level=ctx_level,
    )
    return original_snippet, modified_snippet, start, end


def read_modified_snippet(
    repo_path: str,
    step: dict[str, Any],
    pre_edit: PreEditCapture,
) -> str:
    """Read the same line span from disk after Cursor completes the edit."""
    path = step.get("file") or step.get("where") or ""
    return read_snippet_at_span(
        repo_path,
        path,
        pre_edit.start_line,
        pre_edit.end_line,
    )


def noop_failure_result() -> ValidationResult:
    """Failure when Cursor made no file changes after an edit attempt."""
    return ValidationResult(
        passed=False,
        confidence=1.0,
        reason="Edit did not change any code",
        missing_requirements=[
            "Your edit did not change any code — the instruction may be too vague to locate a target.",
            "Include a code snippet from the file in the instruction or use approximate_line.",
        ],
        noop_diff=True,
    )


def structural_failure_result(errors: list[str]) -> ValidationResult:
    """Failure when post-edit file has broken structure."""
    reqs = list(errors) + ["Restore valid brace/indent structure"]
    return ValidationResult(
        passed=False,
        confidence=1.0,
        reason="; ".join(errors),
        missing_requirements=reqs,
        structural_fail=True,
    )


def build_retry_note(prior: ValidationResult) -> str:
    """Build Cursor retry instruction from prior validation failure."""
    parts = [
        "Previous edit attempt did not satisfy the requested change.",
        f"Reason: {prior.reason}",
    ]
    if prior.noop_diff:
        parts.append(
            "Your edit did not change any code — the instruction may be too vague to locate a target."
        )
        parts.append(
            "Include a code snippet from the file in the instruction or use approximate_line."
        )
    if prior.missing_requirements:
        parts.append("Address these missing requirements:")
        for req in prior.missing_requirements:
            parts.append(f"  - {req}")
    parts.append("Apply the planner instruction completely.")
    return "\n".join(parts)


def _validation_context(
    plan: ExecutionPlan,
    step: dict[str, Any],
    *,
    prior_failure: ValidationResult | None = None,
    structural_warnings: list[str] | None = None,
    intent_marker_in_file: bool | None = None,
    context_level: str | None = None,
) -> dict[str, Any]:
    """Minimal structured context for the validation prompt."""
    scope_note = (
        "Evaluate ONLY whether the modified code satisfies the security property "
        "requested in the planner instruction for the region shown in Original/Modified "
        "Code snippets. Do not judge the security posture of the entire file."
    )
    if context_level == "full_file":
        scope_note = (
            "The snippets contain the full file. Evaluate whether the requested security "
            "property is satisfied at the named semantic target. "
            "Do not fail for unrelated pre-existing issues outside the instruction scope."
        )
    ctx: dict[str, Any] = {
        "ticket_id": plan.ticket_id,
        "step_id": step.get("step_id"),
        "action": step.get("action"),
        "reason": step.get("reason") or step.get("why", ""),
        "instruction": step.get("instruction") or step.get("change") or step.get("target", ""),
        "target_file": step.get("file") or step.get("where") or "",
        "target_type": step.get("target_type"),
        "target_name": step.get("target_name"),
        "caution_flag": step.get("caution_flag"),
        "intent_marker": step.get("intent_marker"),
        "validate": step.get("validate"),
        "risk_level": plan.risk_level,
        "validation_context_level": context_level,
        "edit_scope": scope_note,
        "target_name_note": (
            "target_type/target_name identify the likely vulnerable semantic region — "
            "they are hints for context expansion, not a restriction to declaration lines only."
        ),
    }
    if prior_failure:
        ctx["prior_validation"] = {
            "reason": prior_failure.reason,
            "missing_requirements": prior_failure.missing_requirements,
        }
    if structural_warnings:
        ctx["structural_warnings"] = structural_warnings
        ctx["structural_warning_note"] = (
            "Structural checker reported warnings (may be false positives from JSX, "
            "template literals, or decorators). Use the modified snippet to decide; "
            "do not fail on brace-counting alone unless the code is clearly broken."
        )
    if intent_marker_in_file is not None:
        ctx["intent_marker_present_in_file"] = intent_marker_in_file
    if step.get("caution_flag"):
        ctx["caution_flag_note"] = (
            "Planner reported ambiguous target symbol (multiple occurrences). "
            "Evaluate only whether the instruction was applied to the correct occurrence. "
            "Do not fail because other occurrences of the same symbol remain unchanged."
        )
    return ctx


def _build_validation_prompt(
    validation_policy: str,
    plan: ExecutionPlan,
    step: dict[str, Any],
    *,
    original_snippet: str,
    modified_snippet: str,
    prior_failure: ValidationResult | None = None,
    structural_warnings: list[str] | None = None,
    intent_marker_in_file: bool | None = None,
    context_level: str | None = None,
) -> str:
    """Assemble the edit-verifier prompt (not a whole-file security review)."""
    context = _validation_context(
        plan,
        step,
        prior_failure=prior_failure,
        structural_warnings=structural_warnings,
        intent_marker_in_file=intent_marker_in_file,
        context_level=context_level,
    )
    parts = [
        validation_policy,
        "",
        "---",
        "",
        "You are a SECURITY-INTENT VERIFIER for a single automated code edit.",
        "Your job is to answer: Does the modified code satisfy the requested security property?",
        "Do NOT check whether the code matches the planner's preferred implementation shape.",
        "",
        "## Scope (strict)",
        "- Evaluate ONLY whether the security property in the planner instruction is satisfied "
        "in the code region shown in the snippets below.",
        "- Compare Original Code vs Modified Code for the requested security outcome only.",
        "- The snippets are the edit scope — diff-expanded semantic block or full file fallback.",
        "  Judge the completed edit in that scope; do not fail for code outside it.",
        "- Judge the actual Modified Code shown — do not infer compile errors, type mismatches, "
        "or missing imports unless the snippet clearly shows broken/invalid syntax.",
        "",
        "## PASS when",
        "- The requested security property is satisfied in the modified snippet.",
        "- Equivalent implementations are acceptable even when the instruction names one approach:",
        "  different API calls with the same security guarantee, helper extraction, variable renames,",
        "  formatting, refactoring, Spring ${ENV:default} vs os.environ,",
        "  Jws<Claims>.getBody() vs direct Claims cast, alternate library patterns, etc.",
        "- The fix was already present before edit (original equals modified but instruction satisfied).",
        "",
        "## FAIL when (in-scope only)",
        "- The requested security property is still violated in the edited target.",
        "- The edited target still contains the exact vulnerability the instruction asked to remove.",
        "- The edit inverted or weakened the specific security behavior named in the instruction.",
        "- The requested security change is missing or only partially implemented in the edited target.",
        "",
        "## Do NOT fail because of",
        "- Implementation details that preserve the required security behavior (API choice, helper "
        "methods, variable names, call-chain shape, type-cast style vs getter, etc.).",
        "- The instruction naming one API/pattern while the modified code uses an equivalent one.",
        "- Hypothetical type or compilation issues not visible in the Modified Code snippet.",
        "- Hardcoded secrets, weak crypto, or other vulnerabilities OUTSIDE this step's instruction scope.",
        "- Pre-existing insecure code in other profiles, sections, or lines not part of this edit.",
        "- Unrelated changes elsewhere in the file (log as observations only).",
        "- Other ticket findings the planner did not ask to fix in this step.",
        "",
        "## Observations",
        "- If you notice unrelated security issues, list them in `observations` only.",
        "- Observations MUST NOT affect the `pass` boolean.",
        "",
        "## Intent marker",
        "- If `intent_marker_present_in_file` is true in Planner Context, the marker exists in the file.",
        "- Do not fail solely because the marker line is outside the snippet window.",
        "",
        "Return STRICT JSON only. No markdown. No explanations. No extra text.",
        "",
        "Expected output schema:",
        json.dumps(
            {
                "pass": True,
                "confidence": 0.98,
                "reason": "Top-level password property now uses ${ENV:default} as requested",
                "missing_requirements": [],
                "observations": [],
            },
            indent=2,
        ),
        "",
        "## Planner Context",
        json.dumps(context, indent=2, default=str),
        "",
        "## Original Code (edit scope)",
        "```",
        original_snippet or "(empty)",
        "```",
        "",
        "## Modified Code (edit scope)",
        "```",
        modified_snippet or "(empty)",
        "```",
    ]
    return "\n".join(parts)


def validate_edit(
    llm: BaseChatModel,
    plan: ExecutionPlan,
    step: dict[str, Any],
    *,
    original_snippet: str,
    modified_snippet: str,
    validation_policy: str,
    prior_failure: ValidationResult | None = None,
    structural_warnings: list[str] | None = None,
    intent_marker_in_file: bool | None = None,
    context_level: str | None = None,
    attempt: int = 1,
) -> ValidationResult:
    """Run a single LLM semantic validation for an edit step."""
    path = step.get("file") or step.get("where") or ""
    step_id = step.get("step_id", 0)
    ticket_id = plan.ticket_id

    _log.info(
        "executor_validation_start",
        ticket_id=ticket_id,
        step_id=step_id,
        file=path,
        attempt=attempt,
    )

    prompt = _build_validation_prompt(
        validation_policy,
        plan,
        step,
        original_snippet=original_snippet,
        modified_snippet=modified_snippet,
        prior_failure=prior_failure,
        structural_warnings=structural_warnings,
        intent_marker_in_file=intent_marker_in_file,
        context_level=context_level,
    )

    started = time.perf_counter()
    try:
        response = invoke_tracked(llm, prompt, agent="executor")
        parsed = parse_llm_json(llm_content(response), SemanticValidationResponse)
        result = ValidationResult(
            passed=parsed.passed,
            confidence=parsed.confidence,
            reason=parsed.reason,
            missing_requirements=list(parsed.missing_requirements),
            observations=list(parsed.observations),
        )
    except (ValueError, Exception) as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result = ValidationResult(
            passed=False,
            confidence=0.0,
            reason=f"LLM validation response unparseable: {exc}",
            missing_requirements=[],
        )
        _log.info(
            "executor_validation_failure",
            ticket_id=ticket_id,
            step_id=step_id,
            file=path,
            confidence=0.0,
            validation_time_ms=elapsed_ms,
            reason=result.reason,
            missing_requirements=[],
            attempt=attempt,
        )
        _log.info(
            "executor_validation_complete",
            ticket_id=ticket_id,
            step_id=step_id,
            file=path,
            passed=False,
            total_attempts=attempt,
        )
        return result

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    event = "executor_validation_success" if result.passed else "executor_validation_failure"
    log_kwargs: dict[str, Any] = {
        "ticket_id": ticket_id,
        "step_id": step_id,
        "file": path,
        "confidence": result.confidence,
        "validation_time_ms": elapsed_ms,
        "reason": result.reason,
        "attempt": attempt,
    }
    if not result.passed:
        log_kwargs["missing_requirements"] = result.missing_requirements
    if result.observations:
        log_kwargs["observations"] = result.observations
    _log.info(event, **log_kwargs)
    _log.info(
        "executor_validation_complete",
        ticket_id=ticket_id,
        step_id=step_id,
        file=path,
        passed=result.passed,
        total_attempts=attempt,
    )
    return result


def validate_edit_progressive(
    llm: BaseChatModel,
    plan: ExecutionPlan,
    step: dict[str, Any],
    *,
    repo_path: str,
    pre_edit: PreEditCapture,
    validation_policy: str,
    prior_failure: ValidationResult | None = None,
    structural_warnings: list[str] | None = None,
    intent_marker_in_file: bool | None = None,
    attempt: int = 1,
) -> ValidationResult:
    """Validate with diff-semantic scope first; expand to full file before Cursor repair."""
    path = step.get("file") or step.get("where") or ""
    levels = [ContextLevel.DIFF_SEMANTIC, ContextLevel.FULL_FILE]
    last_result: ValidationResult | None = None

    for idx, level in enumerate(levels):
        original_snippet, modified_snippet, start, end = _validation_snippets(
            repo_path,
            step,
            pre_edit,
            level=level,
        )
        _log.info(
            "executor_validation_context",
            ticket_id=plan.ticket_id,
            step_id=step.get("step_id"),
            file=path,
            context_level=level.value,
            start_line=start,
            end_line=end,
            attempt=attempt,
        )
        result = validate_edit(
            llm,
            plan,
            step,
            original_snippet=original_snippet,
            modified_snippet=modified_snippet,
            validation_policy=validation_policy,
            prior_failure=prior_failure if idx == 0 else last_result,
            structural_warnings=structural_warnings,
            intent_marker_in_file=intent_marker_in_file,
            context_level=level.value,
            attempt=attempt,
        )
        if result.passed:
            return result
        last_result = result
        mod_lines = _read_file_lines(repo_path, path)
        if (
            mod_lines
            and not modified_snippet.strip()
            and level != ContextLevel.FULL_FILE
        ):
            _log.info(
                "executor_validation_expand_context",
                ticket_id=plan.ticket_id,
                step_id=step.get("step_id"),
                file=path,
                from_level=level.value,
                to_level=ContextLevel.FULL_FILE.value,
                reason="empty_modified_snippet",
            )
            continue
        if idx == 0 and warrants_context_expansion(
            result,
            start_line=start,
            end_line=end,
            total_lines=len(mod_lines),
        ):
            _log.info(
                "executor_validation_expand_context",
                ticket_id=plan.ticket_id,
                step_id=step.get("step_id"),
                file=path,
                from_level=ContextLevel.DIFF_SEMANTIC.value,
                to_level=ContextLevel.FULL_FILE.value,
                reason=result.reason[:200],
            )
            continue
        break

    assert last_result is not None
    return last_result


def log_edit_outcome(
    *,
    ticket_id: str,
    step_id: int,
    file_path: str,
    retry_count: int,
    result: ValidationResult,
    noop_diff: bool = False,
) -> None:
    """Structured telemetry for skill iteration — one event per EDIT validation."""
    _log.info(
        "edit_outcome",
        ticket_id=ticket_id,
        step_id=step_id,
        file=file_path,
        retry_count=retry_count,
        structural_fail=result.structural_fail,
        noop_diff=noop_diff or result.noop_diff,
        validation_fail_reason=result.reason if not result.passed else None,
        missing_requirements=result.missing_requirements if not result.passed else [],
        passed=result.passed,
    )
