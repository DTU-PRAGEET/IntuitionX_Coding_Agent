"""LLM semantic validation for executor post-edit checks."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

from src.core.logging import get_logger
from src.core.repo_snapshot import old_string_in_file
from src.core.runner_utils import invoke_tracked, llm_content, parse_llm_json
from src.core.schemas import ExecutionPlan
from src.core.semantic_target import ResolvedTarget, is_semantic_edit, resolve_semantic_target
from src.core.skill_manager import PromptBuilder, format_domain_skills_for_prompt, module_skills_dir

_log = get_logger("executor.validation")

SKILLS_DIR = module_skills_dir("executor")
DEFAULT_PADDING = 3


class ValidationResult(BaseModel):
    """Outcome of a single semantic edit validation."""

    passed: bool
    confidence: float
    reason: str
    missing_requirements: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)


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
    resolved: ResolvedTarget | None
    start_line: int
    end_line: int


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


def capture_pre_edit(
    repo_path: str,
    step: dict[str, Any],
) -> PreEditCapture:
    """Capture original snippet and line span before any Cursor edit."""
    path = step.get("file") or step.get("where") or ""
    if is_semantic_edit(step):
        resolved = resolve_semantic_target(repo_path, step)
        if resolved:
            return PreEditCapture(
                original_snippet=resolved.snippet,
                resolved=resolved,
                start_line=resolved.start_line,
                end_line=resolved.end_line,
            )
        return PreEditCapture(original_snippet="", resolved=None, start_line=1, end_line=1)

    old_string = (step.get("old_string") or "").strip()
    if old_string:
        info = old_string_in_file(repo_path, path, old_string)
        if info.get("exists"):
            full = Path(repo_path) / path
            content = full.read_text(encoding="utf-8")
            idx = content.find(old_string)
            prefix = content[:idx]
            start_line = prefix.count("\n") + 1
            end_line = start_line + old_string.count("\n")
            return PreEditCapture(
                original_snippet=old_string,
                resolved=None,
                start_line=start_line,
                end_line=max(start_line, end_line),
            )
    return PreEditCapture(original_snippet=old_string, resolved=None, start_line=1, end_line=40)


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


def build_retry_note(prior: ValidationResult) -> str:
    """Build Cursor retry instruction from prior validation failure."""
    parts = [
        "Previous edit attempt did not satisfy the requested change.",
        f"Reason: {prior.reason}",
    ]
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
) -> dict[str, Any]:
    """Minimal structured context for the validation prompt."""
    ctx: dict[str, Any] = {
        "ticket_id": plan.ticket_id,
        "step_id": step.get("step_id"),
        "action": step.get("action"),
        "reason": step.get("reason") or step.get("why", ""),
        "instruction": step.get("instruction") or step.get("change") or step.get("target", ""),
        "target_file": step.get("file") or step.get("where") or "",
        "target_type": step.get("target_type"),
        "target_name": step.get("target_name"),
        "intent_marker": step.get("intent_marker"),
        "validate": step.get("validate"),
        "risk_level": plan.risk_level,
        "edit_scope": (
            "Evaluate ONLY whether the planner instruction was correctly applied "
            "to the semantic target shown in Original/Modified Code snippets. "
            "Do not judge the security posture of the entire file."
        ),
    }
    if not is_semantic_edit(step):
        ctx["old_string"] = step.get("old_string")
        ctx["new_string"] = step.get("new_string")
    if prior_failure:
        ctx["prior_validation"] = {
            "reason": prior_failure.reason,
            "missing_requirements": prior_failure.missing_requirements,
        }
    return ctx


def _build_validation_prompt(
    validation_policy: str,
    plan: ExecutionPlan,
    step: dict[str, Any],
    *,
    original_snippet: str,
    modified_snippet: str,
    prior_failure: ValidationResult | None = None,
) -> str:
    """Assemble the edit-verifier prompt (not a whole-file security review)."""
    context = _validation_context(plan, step, prior_failure=prior_failure)
    parts = [
        validation_policy,
        "",
        "---",
        "",
        "You are an EDIT VERIFIER for a single automated code edit.",
        "Your job is to confirm the planner's requested change was correctly implemented — "
        "NOT to audit the entire file for security.",
        "",
        "## Scope (strict)",
        "- Evaluate ONLY the planner instruction applied to the semantic target in the snippets below.",
        "- Compare Original Code vs Modified Code for the requested change only.",
        "- The snippets are the edit scope plus immediate surrounding context — nothing else exists "
        "for your PASS/FAIL decision.",
        "",
        "## PASS when",
        "- The requested edit is present and semantically correct in the modified snippet.",
        "- Equivalent implementations are acceptable (renames, formatting, helper extraction, "
        "different but equivalent APIs, Spring ${ENV:default} vs os.environ, etc.).",
        "- The fix was already present before edit (original equals modified but instruction satisfied).",
        "",
        "## FAIL when (in-scope only)",
        "- The requested change is missing or only partially implemented in the edited target.",
        "- The edited target still contains the exact vulnerability the instruction asked to remove.",
        "- The edit inverted or weakened the specific property/behavior named in the instruction.",
        "",
        "## Do NOT fail because of",
        "- Hardcoded secrets, weak crypto, or other vulnerabilities OUTSIDE this step's instruction scope.",
        "- Pre-existing insecure code in other profiles, sections, or lines not part of this edit.",
        "- Unrelated changes elsewhere in the file (log as observations only).",
        "- Other ticket findings the planner did not ask to fix in this step.",
        "",
        "## Observations",
        "- If you notice unrelated security issues, list them in `observations` only.",
        "- Observations MUST NOT affect the `pass` boolean.",
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
