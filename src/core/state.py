"""Global AgentState schema — PRD-aligned single source of truth."""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class JiraComment(TypedDict):
    author: str
    body: str
    created: str


class JiraDependency(TypedDict):
    issue_id: str
    relationship: str
    status: str
    summary: str


class JiraAttachment(TypedDict):
    name: str
    type: str


class JiraTicket(TypedDict):
    issue_id: str
    title: str
    issue_type: str
    status: str
    priority_raw: str
    priority_normalized: Literal["P0", "P1", "P2", "P3", "P4"]
    module: str
    problem_statement: str
    acceptance_criteria: list[str] | str
    assignee: str
    reporter: str
    labels: list[str]
    repo_links: list[str]
    dependencies: list[JiraDependency]
    attachments: list[JiraAttachment]
    comments: list[JiraComment]
    ticket_history_summary: dict
    flags: list[str]
    vulnerability_category: str


class Hypothesis(TypedDict):
    rank: int
    hypothesis: str
    evidence_strength: Literal["strong", "moderate", "weak"]
    evidence: list[str]


class AffectedFile(TypedDict):
    path: str
    reason: str
    evidence_strength: Literal["strong", "moderate", "weak"]


class ResearchOutput(TypedDict):
    ticket_id: str
    research_summary: str
    hypotheses: list[Hypothesis]
    affected_files: list[AffectedFile]
    affected_modules: list[str]
    module_blast_radius: list[str]
    architectural_risk: Literal["low", "medium", "high"]
    issue_type_assessment: str
    flags: list[str]


class ExecutionStep(TypedDict):
    step_id: int
    action: Literal["READ", "EDIT", "VERIFY", "RUN_TESTS"]
    file: str
    target: str
    reason: str
    change: Optional[str]
    old_string: Optional[str]
    new_string: Optional[str]
    intent_marker: Optional[str]


class PlanOutput(TypedDict):
    ticket_id: str
    root_hypothesis: str
    files_to_change: list[str]
    implementation_constraints: list[str]
    execution_steps: list[ExecutionStep]
    write_tests: bool
    create_files: bool
    risk_level: Literal["low", "medium", "high"]
    self_healing_notes: list[str]
    flags: list[str]


class ExecutionOutput(TypedDict):
    mr_url: str
    changed_files: list[str]
    git_diff_summary: str
    worktree_path: str
    branch_name: str
    compilation_status: Literal["PASS", "FAIL", "SKIPPED"]


class HumanApprovalGate(TypedDict):
    gate_id: str
    gate_type: Literal["RESEARCH", "PLANNING", "MICRO_CONFIRM"]
    status: Literal["PENDING", "APPROVED", "REJECTED", "REVISE"]
    feedback: Optional[str]
    timestamp: str


class FailedAttempt(TypedDict):
    attempt_id: str
    ticket_id: str
    phase_failed: str
    error_type: str
    error_summary: str
    plan_hash: Optional[str]
    plan_used: Optional[PlanOutput]
    timestamp: str


class RepoProfile(TypedDict):
    repo_type: Literal["java_gradle", "python", "unknown"]
    root_path: str
    modules: list[str]
    package_roots: list[str]


class AgentState(TypedDict):
    # Inputs
    jira_ticket_id: str
    user_prompt: str
    context_image: Optional[str]
    repo_path: Optional[str]
    git_target_branch: Optional[str]

    # Jira resolved data
    jira_ticket: Optional[JiraTicket]
    repo_profile: Optional[RepoProfile]

    # Orchestrator decisions
    routing_route: Optional[Literal["FULL", "SKIP_RESEARCH", "MICRO", "EXIT"]]
    current_phase: Literal[
        "INIT",
        "ORCHESTRATOR",
        "RESEARCH",
        "PLANNING",
        "IMPLEMENTATION",
        "REVIEW",
        "DONE",
        "EXIT",
    ]
    risk_level: Optional[Literal["LOW", "MEDIUM", "HIGH"]]
    exit_message: Optional[str]

    # Agent outputs
    research_output: Optional[ResearchOutput]
    plan_output: Optional[PlanOutput]
    execution_output: Optional[ExecutionOutput]

    # Human approval gates
    human_gates: list[HumanApprovalGate]

    # Self-healing / memory
    previous_attempts: list[FailedAttempt]
    current_error_log: Optional[str]
    retry_count: int
    max_retries: int
    self_healing_cycle_index: int

    # Token budget
    total_tokens_used: int
    token_budget: int

    # LangGraph native
    messages: Annotated[list, add_messages]

    # Session metadata
    session_id: str
    created_at: str
    updated_at: str
    prompt_version: str
    pending_diff: Optional[str]
    pending_worktree: Optional[str]
    pending_branch: Optional[str]


def initial_state(
    jira_ticket_id: str,
    user_prompt: str = "",
    repo_path: Optional[str] = None,
) -> AgentState:
    """Create a fresh AgentState for a new workflow session."""
    from datetime import datetime, timezone
    import uuid

    now = datetime.now(timezone.utc).isoformat()
    return AgentState(
        jira_ticket_id=jira_ticket_id,
        user_prompt=user_prompt,
        context_image=None,
        repo_path=repo_path,
        git_target_branch=None,
        jira_ticket=None,
        repo_profile=None,
        routing_route=None,
        current_phase="INIT",
        risk_level=None,
        exit_message=None,
        research_output=None,
        plan_output=None,
        execution_output=None,
        human_gates=[],
        previous_attempts=[],
        current_error_log=None,
        retry_count=0,
        max_retries=3,
        self_healing_cycle_index=0,
        total_tokens_used=0,
        token_budget=150_000,
        messages=[],
        session_id=str(uuid.uuid4()),
        created_at=now,
        updated_at=now,
        prompt_version="phase1-v1",
        pending_diff=None,
        pending_worktree=None,
        pending_branch=None,
    )
