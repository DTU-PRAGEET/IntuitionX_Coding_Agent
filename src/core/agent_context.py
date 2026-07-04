"""Typed per-agent context — minimal handoffs (milestone 1.2 + plan.md ADR-004)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from src.core.state import JiraTicket


class AttemptSummary(BaseModel):
    """Compact failed-attempt row for prompts."""

    phase_failed: str
    error_type: str = "unknown"
    error_summary: str = Field(max_length=300)
    plan_hash: str | None = None


class RepoProfileCompact(BaseModel):
    """Trimmed repo_profile slice for per-agent prompts (type + top modules).
    Embedded in OrchestratorContext / ResearchContext / PlanningContext handoffs."""
    repo_type: str
    modules: list[str] = Field(default_factory=list, max_length=8)


class ResearchHandoff(BaseModel):
    """Planner-facing research slice — no full hypotheses dump."""

    ticket_id: str
    research_summary: str = Field(max_length=800)
    affected_files: list[dict[str, str]] = Field(default_factory=list, max_length=10)
    architectural_risk: str = "medium"
    flags: list[str] = Field(default_factory=list, max_length=5)


class OrchestratorContext(BaseModel):
    """Token-minimal prompt bundle for orchestrator routing LLM (ADR-004).
    Fields mirror `build_orchestrator_routing_context` in `core/orchestrator_context.py`."""
    jira_ticket_id: str
    user_prompt: str = ""
    ticket: dict[str, Any]
    repo_path: str | None = None
    repo_profile: RepoProfileCompact | None = None
    previous_attempts: list[AttemptSummary] = Field(default_factory=list)
    current_error_log: str | None = Field(default=None, max_length=500)
    token_budget: int = 150_000
    tokens_used: int = 0


class ResearchContext(BaseModel):
    """Prompt-facing research-agent context: ticket, repo path, prior failures, budget.
    Companion to portable `AgentContext` for typed per-phase prompt assembly."""
    jira_ticket_id: str
    user_prompt: str = ""
    ticket: dict[str, Any]
    repo_path: str | None = None
    repo_profile: RepoProfileCompact | None = None
    previous_attempts: list[AttemptSummary] = Field(default_factory=list)
    token_budget: int = 150_000
    tokens_used: int = 0


class PlanningContext(BaseModel):
    """Planner prompt context — ResearchHandoff + routing_route + gate feedback.
    Feeds `agents/planner/agent.py` without full research hypothesis dumps."""
    jira_ticket_id: str
    ticket: dict[str, Any]
    research: ResearchHandoff
    routing_route: str = "FULL"
    repo_path: str | None = None
    previous_attempts: list[AttemptSummary] = Field(default_factory=list)
    gate_feedback: str | None = Field(default=None, max_length=400)
    token_budget: int = 150_000
    tokens_used: int = 0


class ExecutorContext(BaseModel):
    """Executor prompt context — files_to_change, constraints, test/file flags.
    Derived from ExecutionPlan / fix_plan for implementation LLM prompts."""
    jira_ticket_id: str
    files_to_change: list[str] = Field(default_factory=list)
    implementation_constraints: list[str] = Field(default_factory=list, max_length=10)
    write_tests: bool = False
    create_files: bool = False
    token_budget: int = 150_000
    tokens_used: int = 0


AgentContextModel = OrchestratorContext | ResearchContext | PlanningContext | ExecutorContext


@dataclass
class AgentContext:
    """Milestone 1.2 portable context passed between SubAgent.run() calls."""

    bug_description: str
    file_paths: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    cursor_history: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_ticket(
        cls,
        ticket: JiraTicket,
        *,
        user_prompt: str = "",
        repo_path: str | None = None,
    ) -> AgentContext:
        """From Ticket for AgentContext."""
        desc = (ticket.get("problem_statement") or ticket.get("title") or "")[:2000]
        return cls(
            bug_description=desc,
            file_paths=list(ticket.get("repo_links") or [])[:5],
            history=[],
            metadata={
                "ticket_id": ticket.get("issue_id", ""),
                "jira_ticket": ticket,
                "user_prompt": user_prompt,
                "repo_path": repo_path,
                "title": ticket.get("title", ""),
                "execution_metadata": {},
            },
        )

    @property
    def research_output(self) -> dict[str, Any]:
        """Research Output for AgentContext."""
        return self.metadata.get("research_output") or {}

    @property
    def fix_plan(self) -> dict[str, Any]:
        """Fix Plan for AgentContext."""
        return self.metadata.get("fix_plan") or {}

    @property
    def plan_output(self) -> dict[str, Any]:
        """Plan Output for AgentContext."""
        return self.metadata.get("plan_output") or {}

    @property
    def execution_metadata(self) -> dict[str, Any]:
        """Execution Metadata for AgentContext."""
        if "execution_metadata" not in self.metadata:
            self.metadata["execution_metadata"] = {}
        return self.metadata["execution_metadata"]

    def append_cursor_history(self, entry: dict[str, Any]) -> AgentContext:
        """Append Cursor History for AgentContext."""
        self.cursor_history.append(entry)
        return self

    def with_phase_output(self, phase: str, artifact: dict[str, Any]) -> AgentContext:
        """With Phase Output for AgentContext."""
        self.metadata[f"{phase}_output"] = artifact
        if phase == "research":
            for f in artifact.get("affected_files") or []:
                path = f.get("path") if isinstance(f, dict) else None
                if path and path not in self.file_paths:
                    self.file_paths.append(path)
        return self
