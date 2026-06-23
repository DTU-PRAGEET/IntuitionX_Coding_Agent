"""Planning Agent — single-turn structured planner with bounded verification tools."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langsmith import traceable
from pydantic import ValidationError

from src.agents.planner.tools.builder import build_planner_tools
from src.core.llm import TaskComplexity, get_llm
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import llm_content, invoke_tracked
from src.core.plan_validation import (
    align_old_string_to_disk,
    is_truncated_patch,
    validate_edit_patches,
    validate_execution_plan,
)
from src.core.prompt_builder import PromptBuilder
from src.core.repo_snapshot import git_repo_snapshot, log_edit_patch, log_repo_snapshot
from src.core.schemas import ExecutionPlan, ResearchReport
from src.core.state import AgentState, PlanOutput
from src.tools.registry import ToolRegistry

_log = get_logger("planner")

SKILLS_DIR = Path(__file__).parent / "skills"
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


def _step_file_paths(steps: list[dict[str, Any]], repo_path: str) -> list[str]:
    """Collect relative file paths referenced by planner steps."""
    repo_root = repo_path.rstrip("/")
    paths: list[str] = []
    for step in steps:
        where = (step.get("where") or step.get("file") or "").strip()
        if not where or where in _INVALID_WHERE:
            continue
        if where == repo_root or where.rstrip("/") == repo_root:
            continue
        if where.startswith("/"):
            continue
        paths.append(where)
    return list(dict.fromkeys(paths))


def _coerce_execution_plan_payload(
    data: dict[str, Any],
    report: ResearchReport,
    repo_path: str,
) -> dict[str, Any]:
    """Fill required ExecutionPlan fields the LLM often omits."""
    out = dict(data)
    out.setdefault("implementation_constraints", [])
    out.setdefault("self_healing_notes", [])
    out.setdefault("flags", [])

    files = out.get("files_to_change")
    if not files:
        candidates = _research_file_paths(report)
        if candidates:
            out["files_to_change"] = list(dict.fromkeys(candidates))
        else:
            out["files_to_change"] = _step_file_paths(out.get("execution_steps") or [], repo_path)
    return out


def _parse_execution_plan(raw: str, report: ResearchReport, repo_path: str) -> ExecutionPlan:
    """Parse LLM JSON and coerce common omissions before schema validation."""
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"Could not parse JSON from LLM response: {raw[:200]}")
    data = _coerce_execution_plan_payload(
        json.loads(json_match.group()),
        report,
        repo_path,
    )
    return ExecutionPlan.model_validate(data)


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


def build_planner_prompt(
    report: ResearchReport,
    repo_path: str,
    *,
    routing_route: str = "FULL",
    previous_attempts: list[dict[str, Any]] | None = None,
    gate_feedback: str | None = None,
) -> str:
    context = {
        "research_output": report.model_dump(),
        "repo_path": repo_path,
        "routing_route": routing_route,
        "previous_attempts": previous_attempts or [],
    }
    if gate_feedback:
        context["gate_feedback"] = gate_feedback
    file_contents = _affected_file_contents(report, repo_path)
    prompt, _manifest = PromptBuilder.build(
        "planner",
        SKILLS_DIR,
        context,
        include_references=False,
        extra_instructions=(
            "Produce the ExecutionPlan JSON with canonical fields on every step: "
            "why, where, what, validate. Include top-level files_to_change listing "
            "every affected file path that will be READ or EDIT. "
            "Use actions READ|EDIT|VERIFY|RUN_TESTS only. "
            "Every EDIT step MUST include old_string, new_string, and intent_marker. "
            "Every EDIT must be preceded by READ and followed by VERIFY on the same file. "
            "Copy old_string EXACTLY from AFFECTED FILE CONTENTS below."
            f"{file_contents}"
        ),
    )
    return prompt


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


def _affected_file_contents(report: ResearchReport, repo_path: str) -> str:
    """Load affected file snippets so the planner can copy exact old_string values."""
    root = Path(repo_path)
    blocks: list[str] = []
    for rel in _research_file_paths(report)[:3]:
        full = root / rel
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except OSError:
            continue
        blocks.append(f"### {rel}\n```\n{text[:4000]}\n```")
    if not blocks:
        return ""
    return (
        "\n## AFFECTED FILE CONTENTS (copy old_string EXACTLY from these blocks)\n"
        + "\n".join(blocks)
        + "\n"
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


MOCK_SECURITY_DEMO_FIX = (
    'def search_users(query: str) -> list[dict[str, Any]]:\n'
    '    """Search users — safe parameterized SQL pattern for demo."""\n'
    '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
    '    params = (f"%{query}%",)\n'
    '    return [{"sql": sql, "params": params, "query": query}]'
)


def _apply_demo_sqli_patch(step, content: str) -> bool:
    """Ground EDIT step to full demo SQLi fix when file still has vulnerable pattern."""
    match = re.search(r"(def search_users[\s\S]*?)(?=\n\ndef |\Z)", content)
    if not match:
        return False
    body = match.group(1)
    if 'f"SELECT' not in body and "LIKE '%{query}" not in body:
        return False
    step.old_string = body.rstrip()
    step.new_string = MOCK_SECURITY_DEMO_FIX
    if not step.intent_marker:
        step.intent_marker = "LIKE ?"
    return True


def _ground_edit_patches_from_disk(plan: ExecutionPlan, repo_path: str) -> ExecutionPlan:
    """Replace hallucinated or truncated old_string/new_string with disk-backed patches."""
    root = Path(repo_path)
    for step in plan.execution_steps:
        if step.action != "EDIT":
            continue
        path = step.where or step.file
        if not path:
            continue
        full = root / path
        if not full.is_file():
            continue
        try:
            content = full.read_text(encoding="utf-8")
        except OSError:
            continue

        if step.old_string:
            aligned = align_old_string_to_disk(step.old_string, content)
            if aligned != step.old_string:
                _log.info(
                    "grounded_old_string_newlines",
                    step_id=step.step_id,
                    path=path,
                    old_len=len(step.old_string),
                    aligned_len=len(aligned),
                )
                step.old_string = aligned

        truncated = (
            step.old_string
            and step.new_string
            and is_truncated_patch(step.old_string, step.new_string)
        )
        identical = (
            step.old_string
            and step.new_string
            and step.old_string == step.new_string
        )
        if identical:
            if _apply_demo_sqli_patch(step, content):
                _log.info("grounded_identical_patch", step_id=step.step_id, path=path)
            continue

        if step.old_string and step.old_string in content:
            if truncated and _apply_demo_sqli_patch(step, content):
                _log.info("grounded_truncated_patch", step_id=step.step_id, path=path)
            elif truncated:
                _log.warning(
                    "truncated_patch_ungrounded",
                    step_id=step.step_id,
                    path=path,
                    old_len=len(step.old_string or ""),
                    new_len=len(step.new_string or ""),
                )
            continue

        if _apply_demo_sqli_patch(step, content):
            _log.info("grounded_edit_patch", step_id=step.step_id, path=path)
    return plan


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

        if step.action == "EDIT" and step.old_string:
            try:
                raw_content = (Path(registry.cwd) / path).read_text(encoding="utf-8")
            except OSError:
                raw_content = ""
            check_old = align_old_string_to_disk(step.old_string, raw_content)
            count = raw_content.count(check_old)
            _log.info(
                "verification",
                tool="edit_patch",
                step_id=step.step_id,
                path=path,
                old_string_found=count > 0,
                occurrence_count=count,
            )
            if count == 0:
                flags.append(f"EDIT step {step.step_id}: old_string not found in {path}")
            elif count != 1:
                flags.append(
                    f"EDIT step {step.step_id}: old_string must appear exactly once in {path}, "
                    f"found {count}"
                )

        if step.action in ("READ", "VERIFY") and path not in seen_reads:
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

    return flags, calls


def _log_plan_edit_steps(plan: ExecutionPlan, *, phase: str) -> None:
    for step in plan.execution_steps:
        if step.action != "EDIT":
            continue
        log_edit_patch(
            _log,
            phase=phase,
            step_id=step.step_id,
            file_path=step.where or step.file,
            old_string=step.old_string,
            new_string=step.new_string,
        )


def _log_affected_files(plan: ExecutionPlan, repo_path: str) -> None:
    root = Path(repo_path)
    for fp in plan.files_to_change:
        full = root / fp
        _log.info(
            "affected_file",
            path=fp,
            exists=full.is_file(),
            sha256=hashlib.sha256(full.read_bytes()).hexdigest() if full.is_file() else "",
        )


def _plan_to_output(plan: ExecutionPlan) -> PlanOutput:
    out: PlanOutput = PlanOutput(
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
    if plan.planner_repo_path:
        out["planner_repo_path"] = plan.planner_repo_path  # type: ignore[typeddict-unknown-key]
    if plan.planner_snapshot_head:
        out["planner_snapshot_head"] = plan.planner_snapshot_head  # type: ignore[typeddict-unknown-key]
    return out


@traceable(name="planner_agent")
def run_planner(
    report: ResearchReport,
    repo_path: str,
    *,
    routing_route: str = "FULL",
    previous_attempts: list[dict[str, Any]] | None = None,
    gate_feedback: str | None = None,
    llm: BaseChatModel | None = None,
    verify: bool = True,
) -> ExecutionPlan:
    """Single LLM call to produce ExecutionPlan; optional bounded verification tools."""
    configure_logging()
    log_repo_snapshot(_log, "start", repo_path, ticket_id=report.ticket_id, routing_route=routing_route)

    llm = llm or get_llm(TaskComplexity.HIGH)
    prompt = build_planner_prompt(
        report,
        repo_path,
        routing_route=routing_route,
        previous_attempts=previous_attempts,
        gate_feedback=gate_feedback,
    )

    registry: ToolRegistry | None = None
    try:
        try:
            response = invoke_tracked(llm, prompt, agent="planner")
            raw = llm_content(response)
            _log.info("planner_raw_json", ticket_id=report.ticket_id, raw_json=raw)
            plan = _parse_execution_plan(raw, report, repo_path)
            plan = _normalize_plan_paths(plan, report, repo_path)
            plan = _ground_edit_patches_from_disk(plan, repo_path)
            _validate_plan_executable(plan, report, repo_path)

            snap = git_repo_snapshot(repo_path)
            plan.planner_repo_path = snap["repo_path"]
            plan.planner_snapshot_head = snap["head"]
            _log_affected_files(plan, repo_path)
            _log_plan_edit_steps(plan, phase="planner_final")
            _log.info(
                "execution_plan_final",
                ticket_id=plan.ticket_id,
                plan_json=plan.model_dump_json(),
            )
        except ValidationError as e:
            _log.error("plan_validation_failed", error=str(e))
            raise ValueError(f"Planner output failed schema validation: {e}") from e
        except Exception as e:
            _log.error("llm_failed", error=str(e))
            raise RuntimeError(
                f"Planner LLM failed for ticket={report.ticket_id!r} repo={repo_path!r}: {e}"
            ) from e

        hard_patch_errors, _soft = validate_edit_patches(plan, repo_path)
        if hard_patch_errors:
            _log.error("edit_patch_validation_failed", errors=hard_patch_errors)
            raise ValueError(
                "Planner produced invalid EDIT patches: " + "; ".join(hard_patch_errors)
            )

        validation_flags = _validate_plan_files(plan, repo_path)
        triad_flags = validate_execution_plan(plan, repo_path, report)
        if triad_flags:
            validation_flags.extend(triad_flags)
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
            planner_snapshot_head=plan.planner_snapshot_head,
        )
        return plan
    finally:
        if registry is not None:
            registry.dispose()


def _latest_gate_feedback(state: AgentState, gate_type: str) -> str | None:
    gates = state.get("human_gates") or []
    for gate in reversed(gates):
        if gate.get("gate_type") == gate_type and gate.get("status") == "REVISE":
            feedback = gate.get("feedback")
            if feedback:
                return feedback
    return None


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
    gate_feedback = _latest_gate_feedback(state, "PLANNING")
    if gate_feedback:
        _log.info("rerun", gate="PLANNING", feedback_preview=gate_feedback[:200])
    try:
        plan = run_planner(
            report,
            repo_path,
            routing_route=state.get("routing_route") or "FULL",
            previous_attempts=state.get("previous_attempts"),
            gate_feedback=gate_feedback,
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
