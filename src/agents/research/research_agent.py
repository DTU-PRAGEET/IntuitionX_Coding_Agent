"""ResearchAgent — unified Deep Agent entry for production and sub-agent paths."""

from __future__ import annotations

from pathlib import Path

from langchain_core.language_models import BaseChatModel

from src.agents.research.deep_runner import (
    build_research_agent,
    build_research_user_message,
    load_skill_manifest_safe,
    _resolve_research_report,
)
from src.agents.research.memory import InMemoryResearchMemory, ResearchMemoryStore
from src.agents.research.tools.builder import build_research_tool_registry
from src.agents.research.tools.file_discovery import extract_anchors
from src.agents.research.tools.repo_profile import profile_repository
from src.core.file_paths import extract_repo_file_paths_from_texts
from src.core.logging import configure_logging, get_logger
from src.core.skill_scripts.budget import init_budget
from src.core.token_usage import record_messages_usage
from src.core.schemas import AffectedFile, ResearchReport
from src.core.state import JiraTicket, RepoProfile
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway

_log = get_logger("research_agent")

# Cursor-first flow: one broad cursor_search to locate code, optionally one more for
# large/unresolved repos. Capped at 2 to conserve limited Cursor agent requests.
_CURSOR_SEARCH_BUDGET = 2
_FALSE_REPO_BLOCKERS = (
    "repo_path_not_found",
    "not checked out",
    "not mounted",
    "repo not found",
    "not accessible",
    "cannot open files",
    "cannot apply or verify",
)


def _sanitize_research_report(report: ResearchReport, repo_path: str) -> ResearchReport:
    """Drop false repo-missing blockers when the checkout is present on disk."""
    root = Path(repo_path)
    if not root.exists():
        return report

    flags = list(report.flags)
    removed = [f for f in flags if any(marker in f.lower() for marker in _FALSE_REPO_BLOCKERS)]
    if not removed:
        return report

    flags = [f for f in flags if f not in removed]
    summary = report.research_summary
    if any(marker in summary.lower() for marker in _FALSE_REPO_BLOCKERS):
        summary = (
            f"{summary}\n\n[auto-corrected] Repository verified at {root}. "
            "Prior repo_path_not_found flag removed — continue investigation from cursor_search hits."
        ).strip()

    return report.model_copy(update={"flags": flags, "research_summary": summary})


def _backfill_affected_files_from_hypotheses(
    report: ResearchReport,
    repo_path: str,
) -> ResearchReport:
    """Ensure every file cited in hypothesis evidence appears in affected_files."""
    root = Path(repo_path)
    if not root.exists():
        return report

    existing = {f.path for f in report.affected_files}
    strength_by_path: dict[str, str] = {f.path: f.evidence_strength for f in report.affected_files}
    for hypothesis in report.hypotheses:
        for path in extract_repo_file_paths_from_texts(hypothesis.evidence, repo_path):
            if path not in strength_by_path:
                strength_by_path[path] = hypothesis.evidence_strength

    added: list[AffectedFile] = []
    for path, strength in strength_by_path.items():
        if path in existing:
            continue
        if not (root / path).is_file():
            continue
        added.append(
            AffectedFile(
                path=path,
                reason=f"Backfilled from hypothesis evidence ({strength})",
                evidence_strength=strength,
            )
        )
        existing.add(path)

    if not added:
        return report

    flags = list(report.flags)
    if "affected_files_backfilled" not in flags:
        flags.append("affected_files_backfilled")

    return report.model_copy(
        update={
            "affected_files": list(report.affected_files) + added,
            "flags": flags,
        }
    )


def _finalize_research_report(report: ResearchReport, repo_path: str) -> ResearchReport:
    report = _sanitize_research_report(report, repo_path)
    return _backfill_affected_files_from_hypotheses(report, repo_path)


class ResearchAgent:
    """LangChain Deep Agent — repository navigation delegated to CursorGateway.search()."""

    def __init__(
        self,
        repo_path: str,
        *,
        gateway: CursorGateway | None = None,
        dry_run: bool = False,
    ):
        self.repo_path = str(Path(repo_path).resolve())
        self.dry_run = dry_run
        self._gateway = gateway

    @property
    def gateway(self) -> CursorGateway:
        if self._gateway is None:
            self._gateway = get_cursor_gateway(self.repo_path, dry_run=self.dry_run)
        return self._gateway

    def run(
        self,
        ticket: JiraTicket,
        *,
        user_prompt: str = "",
        repo_profile: RepoProfile | None = None,
        anchors: list[str] | None = None,
        llm: BaseChatModel | None = None,
        memory: ResearchMemoryStore | None = None,
    ) -> ResearchReport:
        if self.dry_run:
            from src.integrations.cursor.research_runner import mock_research_report

            return mock_research_report(ticket)

        if not Path(self.repo_path).exists():
            raise RuntimeError(
                f"repo_path does not exist on disk: {self.repo_path!r}. "
                "Cannot proceed — check worktree checkout."
            )

        configure_logging()
        from src.core.llm import TaskComplexity, get_llm

        memory = memory or InMemoryResearchMemory()
        anchors = anchors if anchors is not None else extract_anchors(ticket, user_prompt)
        if repo_profile is None and Path(self.repo_path).exists():
            repo_profile = profile_repository(self.repo_path)
        llm = llm or get_llm(TaskComplexity.HIGH)

        manifest = load_skill_manifest_safe()
        thread_id = ticket.get("issue_id") or "research"
        init_budget("research", thread_id, manifest.tool_budget)

        memory.record("research_start", {"ticket_id": ticket.get("issue_id"), "repo_path": self.repo_path})
        _log.info(
            "start",
            ticket_id=ticket.get("issue_id"),
            repo_path=self.repo_path,
            anchors=anchors[:10],
            tool_budget=manifest.tool_budget,
            recursion_limit=manifest.recursion_limit,
            dry_run=self.dry_run,
        )

        _, tools = build_research_tool_registry(
            self.repo_path,
            gateway=self.gateway,
            thread_id=thread_id,
            hard_limit=_CURSOR_SEARCH_BUDGET,
            memory=memory,
        )
        user_message = build_research_user_message(
            ticket,
            self.repo_path,
            repo_profile,
            anchors,
            user_prompt,
            memory,
        )

        agent, harness = build_research_agent(llm, tools, manifest.tool_budget)
        recursion_limit = max(manifest.recursion_limit, manifest.tool_budget * 4 + 8)

        stage = "setup"
        try:
            memory.record("agent_invoke", {"harness": harness, "tool_count": len(tools)})
            stage = "agent.invoke"
            result = agent.invoke(
                {"messages": [{"role": "user", "content": user_message}]},
                config={"recursion_limit": recursion_limit},
            )
            record_messages_usage("research", result.get("messages", []))

            stage = "_resolve_research_report"
            report = _finalize_research_report(
                _resolve_research_report(result),
                self.repo_path,
            )
            memory.record(
                "agent_complete",
                {"response_preview": report.research_summary[:500]},
            )
            _log.info("complete", ticket_id=report.ticket_id, harness=harness)
            return report
        except Exception as e:
            memory.record("agent_error", {"error": str(e), "harness": harness, "stage": stage})
            _log.exception("failed", error=str(e), harness=harness, stage=stage)
            raise RuntimeError(
                f"Research agent failed for ticket={ticket.get('issue_id')!r} "
                f"repo={self.repo_path!r}: {e}"
            ) from e
