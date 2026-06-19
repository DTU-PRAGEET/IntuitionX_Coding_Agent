"""Pydantic models — single source of truth for tools and agent I/O."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


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
    path: str = Field(description="Relative path within workspace root")
    start_line: int = Field(default=1, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)


class WriteInput(BaseModel):
    path: str = Field(description="Relative path within workspace root")
    content: str = Field(description="Full file content to write")


class EditInput(BaseModel):
    path: str = Field(description="Relative path within workspace root")
    old_string: str = Field(description="Exact string to replace — must appear once")
    new_string: str = Field(description="Replacement string")


class BashInput(BaseModel):
    command: str = Field(description="Shell command to run in workspace cwd")
    timeout: int = Field(default=30, ge=1, le=300)


class FindInput(BaseModel):
    pattern: str = Field(description="Glob pattern e.g. **/*.java")
    path: str = Field(default=".", description="Search root relative to workspace")
    limit: int = Field(default=1000, ge=1, le=5000)


class GrepInput(BaseModel):
    pattern: str = Field(description="Regex pattern to search")
    path: str = Field(default=".", description="File or directory relative to workspace")
    glob_pattern: str = Field(default="**/*", description="Glob filter when searching directory")
    limit: int = Field(default=100, ge=1, le=5000)


class LsInput(BaseModel):
    path: str = Field(default=".", description="Directory to list relative to workspace")
    limit: int = Field(default=500, ge=1, le=5000)


# ── Skills ───────────────────────────────────────────────────────────────────


class Skill(BaseModel):
    """Ticket-type skill loaded from markdown (script_name / script_description / script_content)."""

    name: str = Field(description="script_name — unique skill identifier")
    description: str = Field(description="script_description — short summary for logs")
    content: str = Field(description="script_content — multi-line prompt body")
    source_path: str = Field(description="Path to the skill file for debugging")


# ── Agent outputs ────────────────────────────────────────────────────────────


class Hypothesis(BaseModel):
    rank: int
    hypothesis: str
    evidence_strength: Literal["strong", "moderate", "weak"]
    evidence: list[str]


class AffectedFile(BaseModel):
    path: str
    reason: str
    evidence_strength: Literal["strong", "moderate", "weak"]


class ResearchReport(BaseModel):
    ticket_id: str
    research_summary: str
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)
    affected_files: list[AffectedFile]
    affected_modules: list[str]
    module_blast_radius: list[str]
    architectural_risk: Literal["low", "medium", "high"]
    issue_type_assessment: Literal[
        "code_bug", "feature", "environment", "config", "unknown"
    ]
    flags: list[str] = Field(default_factory=list)


class ExecutionStep(BaseModel):
    model_config = {"populate_by_name": True}

    step_id: int
    action: Literal["LOCATE", "EDIT", "VERIFY", "SEARCH"]
    file: str = Field(default="", description="Legacy file path — prefer where")
    target: str = Field(default="", description="Legacy target — prefer what")
    reason: str = Field(default="", description="Legacy reason — prefer why")
    why: str = Field(default="", description="Why this step is needed")
    where: str = Field(default="", description="File path for this step")
    what: str = Field(default="", description="What to do at this location")
    step_validate: str = Field(
        default="",
        alias="validate",
        description="How to verify success",
    )
    change: Optional[str] = Field(
        default=None, description="Human-readable change — executor LLM fallback"
    )
    old_string: Optional[str] = Field(
        default=None, description="Planner-produced patch: exact text to replace"
    )
    new_string: Optional[str] = Field(
        default=None, description="Planner-produced patch: replacement text"
    )

    @model_validator(mode="after")
    def _sync_and_validate(self) -> ExecutionStep:
        if self.file and not self.where:
            self.where = self.file
        elif self.where and not self.file:
            self.file = self.where
        if self.target and not self.what:
            self.what = self.target
        elif self.what and not self.target:
            self.target = self.what
        if self.reason and not self.why:
            self.why = self.reason
        elif self.why and not self.reason:
            self.reason = self.why
        if not self.step_validate:
            if self.action == "VERIFY":
                self.step_validate = self.what or "Verify expected outcome"
            else:
                self.step_validate = f"Confirm {self.action} completed on {self.where or 'target'}"

        if not self.where:
            raise ValueError(f"step {self.step_id}: missing required field 'where' (file path)")
        if not self.what:
            raise ValueError(f"step {self.step_id}: missing required field 'what'")
        if not self.why:
            raise ValueError(f"step {self.step_id}: missing required field 'why'")
        if self.action == "EDIT" and not self.new_string:
            raise ValueError(f"step {self.step_id}: EDIT requires new_string")
        return self


class ExecutionPlan(BaseModel):
    ticket_id: str
    root_hypothesis: str
    files_to_change: list[str]
    implementation_constraints: list[str] = Field(default_factory=list)
    execution_steps: list[ExecutionStep]
    write_tests: bool = False
    create_files: bool = False
    risk_level: Literal["low", "medium", "high"]
    self_healing_notes: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    routing_route: Literal["FULL", "SKIP_RESEARCH", "MICRO", "EXIT"]
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reasoning: str


class FailedAttemptRecord(BaseModel):
    attempt_id: str
    ticket_id: str
    phase_failed: Literal["IMPLEMENTATION", "CICD", "PLANNING_REJECTED", "RESEARCH", "ORCHESTRATOR"]
    error_type: str = "unknown"
    error_summary: str
    plan_hash: Optional[str] = None
    plan_used: Optional[dict[str, Any]] = None
    timestamp: str


class ApprovalDecision(BaseModel):
    status: Literal["APPROVE", "REJECT", "REVISE"]
    feedback: Optional[str] = None
