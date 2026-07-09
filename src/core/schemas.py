"""Pydantic models — single source of truth for tools and agent I/O."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Tool layer ───────────────────────────────────────────────────────────────


class ToolResult(BaseModel):
    """Structured result returned by every hardened tool."""

    output: str
    exit_code: int = 0
    is_error: bool = False
    truncated: bool = False
    log_file_path: Optional[str] = None
    latency_ms: int = 0
    tool_name: str = ""

    def to_llm_string(self) -> str:
        """Serialize for LLM consumption."""
        import json

        return json.dumps(self.model_dump(), indent=2)


class ReadInput(BaseModel):
    """Args schema for the read tool — file path plus optional line window.
    Validated by `tools/read_tool.py` (input_schema) before `read_file` runs.
    Used by executor/planner/research agents via the shared ToolRegistry."""
    path: str = Field(description="Relative path within workspace root")
    start_line: int = Field(default=1, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)


class WriteInput(BaseModel):
    """Args schema for write tool — full file create/overwrite.
    Consumed by `tools/write_tool.py`; surfaced to LLM agents through ToolRegistry."""
    path: str = Field(description="Relative path within workspace root")
    content: str = Field(description="Full file content to write")


class EditReplacementInput(BaseModel):
    """One old_text/new_text pair inside an EditInput.edits[] batch.
    Parsed from LLM `oldText`/`newText` aliases; applied by `tools/edit_tool.py` + `edit_diff.py`."""
    model_config = {"populate_by_name": True}

    old_string: str = Field(
        alias="oldText",
        description="Exact text for one targeted replacement; must be unique in the file",
    )
    new_string: str = Field(alias="newText", description="Replacement text for this edit")


class EditInput(BaseModel):
    """Args schema for targeted file edits (batch or legacy single replacement).
    Validated in `tools/edit_tool.py`; fuzzy matching delegated to `tools/edit_diff.py`."""
    model_config = {"populate_by_name": True}

    path: str = Field(description="Relative path within workspace root")
    edits: list[EditReplacementInput] | None = Field(
        default=None,
        description=(
            "One or more targeted replacements matched against the original file. "
            "Do not include overlapping edits."
        ),
    )
    old_string: str | None = Field(
        default=None,
        description=(
            "Legacy single-edit: exact text to replace (copy verbatim from read output). "
            "Must appear exactly once after normalization."
        ),
    )
    new_string: str | None = Field(
        default=None,
        description="Legacy single-edit: replacement text",
    )

    @model_validator(mode="after")
    def _require_at_least_one_edit(self) -> EditInput:
        """Require At Least One Edit for EditInput."""
        has_edits = bool(self.edits)
        has_legacy = self.old_string is not None or self.new_string is not None
        if not has_edits and not has_legacy:
            raise ValueError("Provide edits[] or both old_string and new_string")
        if has_legacy and (self.old_string is None or self.new_string is None):
            raise ValueError("Both old_string and new_string are required for legacy single-edit calls")
        return self


class BashInput(BaseModel):
    """Args schema for sandboxed shell commands in the workspace cwd.
    Enforced by `tools/bash_tool.py` with timeout caps from config."""
    command: str = Field(description="Shell command to run in workspace cwd")
    timeout: int = Field(default=30, ge=1, le=300)


class FindInput(BaseModel):
    """Args schema for glob file discovery under a repo-relative root.
    Used by `tools/find_tool.py` before agents read candidate paths."""
    pattern: str = Field(description="Glob pattern e.g. **/*.java")
    path: str = Field(default=".", description="Search root relative to workspace")
    limit: int = Field(default=1000, ge=1, le=5000)


class GrepInput(BaseModel):
    """Args schema for regex search with optional glob filter.
    Implemented in `tools/grep_tool.py`; outputs truncated via `tools/truncate.py`."""
    pattern: str = Field(description="Regex pattern to search")
    path: str = Field(default=".", description="File or directory relative to workspace")
    glob_pattern: str = Field(default="**/*", description="Glob filter when searching directory")
    limit: int = Field(default=100, ge=1, le=5000)


class LsInput(BaseModel):
    """Args schema for directory listing within the workspace sandbox.
    Used by `tools/ls_tool.py` during research and executor tool loops."""
    path: str = Field(default=".", description="Directory to list relative to workspace")
    limit: int = Field(default=500, ge=1, le=5000)


# ── Skills ───────────────────────────────────────────────────────────────────


class Skill(BaseModel):
    """Ticket-type skill loaded from markdown (script_name / script_description / script_content)."""

    name: str = Field(description="script_name — unique skill identifier")
    description: str = Field(description="script_description — short summary for logs")
    content: str = Field(description="script_content — multi-line prompt body")
    source_path: str = Field(description="Path to the skill file for debugging")


class SkillManifest(BaseModel):
    """Level-1 SKILL.md frontmatter — always cheap to load."""

    name: str
    description: str
    tool_budget: int = Field(default=10, ge=1)
    recursion_limit: int = Field(default=5, ge=1)
    source_path: str = ""


# ── Agent outputs ────────────────────────────────────────────────────────────


class Hypothesis(BaseModel):
    """Ranked root-cause guess with evidence strength — element of ResearchReport.
    Produced by research structured output (`agents/research/deep_runner.py`).
    Mirrors TypedDict `Hypothesis` in `core/state.py` for LangGraph state."""
    rank: int
    hypothesis: str
    evidence_strength: Literal["strong", "moderate", "weak"]
    evidence: list[str]


class AffectedFile(BaseModel):
    """File implicated by research with reason and evidence strength.
    Filled by ResearchReport; feeds semantic planner via `integrations/cursor/semantic_plan_runner.py`."""
    path: str
    reason: str
    evidence_strength: Literal["strong", "moderate", "weak"]


class ResearchReport(BaseModel):
    """Pydantic research output — hypotheses, affected files, blast radius, risk.
    LLM response_format in `agents/research/deep_runner.py`; validated before planner handoff.
    Converted from `ResearchOutput` via `plan_runner.research_output_to_report`."""
    ticket_id: str
    research_summary: str
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=10)
    affected_files: list[AffectedFile]
    affected_modules: list[str]
    module_blast_radius: list[str]
    architectural_risk: Literal["low", "medium", "high"]
    issue_type_assessment: Literal[
        "code_bug", "feature", "environment", "config", "unknown"
    ]
    flags: list[str] = Field(default_factory=list)


FixType = Literal[
    "parameterize_sql",
    "sanitize_input",
    "auth_check",
    "dependency_upgrade",
    "secrets_removal",
    "xss_encoding",
    "other",
]

TargetType = Literal[
    "function",
    "method",
    "class",
    "variable",
    "assignment",
    "import",
    "block",
    "file",
]


class FixTarget(BaseModel):
    """One file/line span to fix inside a FixPlan (fix_type + rationale).
    Built by planner `run_cursor_plan` / `mock_fix_plan` in `integrations/cursor/plan_runner.py`.
    Consumed by `implement_runner` and `skill_manager._matches_fix_plan`."""
    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    fix_type: FixType
    rationale: str


class FixPlan(BaseModel):
    """Planner contract: root cause, primary_fix_type, and FixTarget list (no patch diffs).
    Emitted by `agents/planner/sub_agent.CursorPlanSubAgent`; consumed by `agents/executor/sub_agent` → `PatchResult`.
    Also stored on `AgentContext.fix_plan` and passed to `CursorGateway.edit()`."""
    ticket_id: str
    root_cause: str
    primary_fix_type: FixType
    targets: list[FixTarget] = Field(default_factory=list)
    files_to_change: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    

class PatchResult(BaseModel):
    """Typed output from the Implementation SubAgent after Cursor edits."""

    ticket_id: str
    status: Literal["success", "partial", "failed", "dry_run"]
    changed_files: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    git_diff_summary: str = ""
    git_diff_full: str = ""
    worktree_path: str = ""
    branch_name: str = ""
    mr_url: str = ""
    compilation_status: str = "SKIPPED"
    flags: list[str] = Field(default_factory=list)
    cursor_status: str = ""
    errors: list[str] = Field(default_factory=list)


class ExecutionStep(BaseModel):
    """One READ/EDIT/VERIFY/RUN_TESTS step inside an ExecutionPlan.
    Authored by `agents/planner/agent.py`; executed in `agents/executor/agent.py`.
    Legacy where/what/why keys are normalized in the model validator."""
    model_config = {"populate_by_name": True}

    step_id: int
    action: Literal["READ", "EDIT", "VERIFY", "RUN_TESTS"]
    file: str = Field(default="", description="File path for this step")
    target: str = Field(default="", description="What to do at this location")
    reason: str = Field(default="", description="Why this step is needed")
    step_validate: str = Field(
        default="",
        alias="validate",
        description="How to verify success",
    )
    intent_marker: Optional[str] = Field(
        default=None,
        description="Unique string expected in file after EDIT",
    )
    change: Optional[str] = Field(
        default=None, description="Human-readable change — executor LLM fallback"
    )
    old_string: Optional[str] = Field(
        default=None, description="Legacy patch: exact text to replace (deprecated)"
    )
    new_string: Optional[str] = Field(
        default=None, description="Legacy patch: replacement text (deprecated)"
    )
    target_type: Optional[TargetType] = Field(
        default=None, description="Semantic target kind for EDIT steps"
    )
    target_name: Optional[str] = Field(
        default=None, description="Symbol or identifier to edit"
    )
    approximate_line: Optional[int] = Field(
        default=None, ge=1, description="Optional line hint for target resolution"
    )
    instruction: Optional[str] = Field(
        default=None, description="Actionable edit directive for EDIT steps"
    )
    parent_name: Optional[str] = Field(
        default=None, description="Enclosing class/module/function for disambiguation"
    )
    occurrence_index: Optional[int] = Field(
        default=None, ge=0, description="0-based index when symbol repeats"
    )
    end_line: Optional[int] = Field(
        default=None, ge=1, description="End line for block targets"
    )
    read_start_line: Optional[int] = Field(
        default=None, ge=1, description="Optional line range start for READ step"
    )
    read_end_line: Optional[int] = Field(
        default=None, ge=1, description="Optional line range end for READ step"
    )
    caution_flag: Optional[str] = Field(
        default=None,
        description="Ambiguity warning for executor when target symbol appears multiple times",
    )

    @model_validator(mode="before")
    @classmethod
    def _map_legacy_fields(cls, data: Any) -> Any:
        """Map legacy where/what/why keys to file/target/reason for backward compatibility."""
        if not isinstance(data, dict):
            return data
        mapped = dict(data)
        if mapped.get("where") and not mapped.get("file"):
            mapped["file"] = mapped["where"]
        if mapped.get("what") and not mapped.get("target"):
            mapped["target"] = mapped["what"]
        if mapped.get("why") and not mapped.get("reason"):
            mapped["reason"] = mapped["why"]
        for legacy in ("where", "what", "why"):
            mapped.pop(legacy, None)
        return mapped

    @model_validator(mode="after")
    def _validate(self) -> ExecutionStep:
        """Validate for ExecutionStep."""
        if not self.step_validate:
            if self.action == "VERIFY":
                self.step_validate = self.target or "Verify expected outcome"
            else:
                self.step_validate = f"Confirm {self.action} completed on {self.file or 'target'}"

        if not self.file:
            raise ValueError(f"step {self.step_id}: missing required field 'file' (file path)")
        if not self.target:
            raise ValueError(f"step {self.step_id}: missing required field 'target'")
        if not self.reason:
            raise ValueError(f"step {self.step_id}: missing required field 'reason'")
        if self.action == "EDIT":
            if not (self.instruction or "").strip():
                raise ValueError(f"step {self.step_id}: EDIT requires instruction")
            if not (self.intent_marker or "").strip():
                raise ValueError(f"step {self.step_id}: EDIT requires intent_marker")
            if not self.target_type:
                raise ValueError(f"step {self.step_id}: EDIT requires target_type")
            if self.target_type != "file" and not (self.target_name or "").strip():
                raise ValueError(
                    f"step {self.step_id}: EDIT requires target_name for {self.target_type}"
                )
            if self.old_string or self.new_string:
                raise ValueError(
                    f"step {self.step_id}: legacy patch fields deprecated; use instruction"
                )
        if self.action == "VERIFY" and not self.intent_marker and not self.step_validate:
            raise ValueError(f"step {self.step_id}: VERIFY requires intent_marker or validate")
        return self


class ExecutionPlan(BaseModel):
    """Step-based plan with semantic EDIT steps — planner output for the executor.
    Built in `agents/planner/agent.py`; validated by `core/plan_validation.py`.
    Stored as `plan_output` on LangGraph AgentState and AgentContext metadata."""
    ticket_id: str
    root_hypothesis: str
    files_to_change: list[str] = Field(default_factory=list)
    implementation_constraints: list[str] = Field(default_factory=list)
    execution_steps: list[ExecutionStep]
    write_tests: bool = False
    create_files: bool = False
    risk_level: Literal["low", "medium", "high"]
    self_healing_notes: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    planner_repo_path: Optional[str] = Field(
        default=None,
        description="Absolute path to the repo snapshot the planner used",
    )
    planner_snapshot_head: Optional[str] = Field(
        default=None,
        description="git rev-parse HEAD at planner time — executor must match before EDIT",
    )


class RoutingDecision(BaseModel):
    """Orchestrator route: FULL / MICRO / EXIT plus risk tier.
    Parsed in `agents/orchestrator/agent.py`; sanitized in `core/orchestrator_context.py`.
    Legacy SKIP_RESEARCH from the LLM is coerced to MICRO."""
    routing_route: Literal["FULL", "MICRO", "EXIT"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reasoning: str
    vulnerability_category: Optional[str] = Field(
        default=None,
        description="OWASP-style category inferred from ticket text (e.g. Cryptographic Failures)",
    )
    recommended_security_skill: Optional[str] = Field(
        default=None,
        description="Canonical security skill stem for research (e.g. cryptographic-failures)",
    )

    @field_validator("routing_route", mode="before")
    @classmethod
    def _coerce_routing_route(cls, value: Any) -> str:
        if value is None:
            return "FULL"
        route = str(value).strip().upper()
        if route == "SKIP_RESEARCH":
            return "MICRO"
        if route in ("FULL", "MICRO", "EXIT"):
            return route
        return "FULL"


class FailedAttemptRecord(BaseModel):
    """Self-healing failure record — phase, error summary, optional plan hash/snapshot.
    Persisted via `memory/cache.save_failed_attempt` from executor and API CI/CD webhook.
    Surfaced on retry through orchestrator context and `AgentState.previous_attempts`."""
    attempt_id: str
    ticket_id: str
    phase_failed: Literal["IMPLEMENTATION", "CICD", "PLANNING_REJECTED", "RESEARCH", "ORCHESTRATOR"]
    error_type: str = "unknown"
    error_summary: str
    plan_hash: Optional[str] = None
    plan_used: Optional[dict[str, Any]] = None
    timestamp: str


class ApprovalDecision(BaseModel):
    """HITL resume payload: APPROVE / REJECT / REVISE with optional feedback.
    Posted to `api/main.approve_workflow` to resume a paused LangGraph thread."""
    status: Literal["APPROVE", "REJECT", "REVISE"]
    feedback: Optional[str] = None
