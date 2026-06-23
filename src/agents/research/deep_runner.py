"""Research Deep Agent runner — LangChain deepagents with ReAct fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from src.agents.research.memory import InMemoryResearchMemory, ResearchMemoryStore
from src.agents.research.tools.builder import build_research_tool_registry
from src.agents.research.tools.file_discovery import rank_candidate_files
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import llm_content, parse_llm_json
from src.core.skill_scripts.budget import init_budget
from src.core.skill_scripts.file_index import build_file_index
from src.core.token_usage import record_messages_usage
from src.core.schemas import ResearchReport, Skill
from src.core.skills_loader import load_skill_manifest, select_research_skill
from src.core.state import JiraTicket, RepoProfile

_log = get_logger("research_deep")

# deepagents expects skills/research/SKILL.md (skill name matches directory)
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_DIR = SKILLS_ROOT / "research"

# Exclude deepagents FS tools except read_file (needed for skill progressive disclosure)
DEEPAGENTS_FS_TOOLS = frozenset(
    {"ls", "write_file", "edit_file", "glob", "grep", "execute"}
)
HARNESS_PROFILE_KEY = "sprinklr-research"
DEEPAGENTS_SKILL_SOURCE = "/"


def _register_research_harness_profile(tool_budget: int) -> None:
    """Hide deepagents built-in filesystem tools — we use src/tools primitives for repo work."""
    try:
        from deepagents import HarnessProfile, register_harness_profile

        register_harness_profile(
            HARNESS_PROFILE_KEY,
            HarnessProfile(
                excluded_tools=DEEPAGENTS_FS_TOOLS,
                system_prompt_suffix=(
                    "Use ONLY the provided read/grep/find/bash/ls tools for repository investigation. "
                    "Use read_file only for loading skill files listed in the Skills System section. "
                    f"Never call write_file or edit_file. Max {tool_budget} tool iterations."
                ),
            ),
        )
    except ImportError:
        pass


def _research_system_prompt(tool_budget: int) -> str:
    """Minimal user instructions — skill body loads via deepagents progressive disclosure."""
    return (
        "Investigate the Jira ticket using the research skill and repository tools. "
        f"Hard limit: {tool_budget} tool calls. "
        "Return a complete ResearchReport JSON when completion criteria are met."
    )


def _build_skills_backend():
    from deepagents.backends import FilesystemBackend

    return FilesystemBackend(root_dir=str(SKILLS_ROOT.resolve()), virtual_mode=True)


def _build_deep_agent(llm: BaseChatModel, tools: list, tool_budget: int):
    """Create deep agent with skills= progressive disclosure (Python 3.11+)."""
    from deepagents import create_deep_agent

    _register_research_harness_profile(tool_budget)
    return create_deep_agent(
        model=llm,
        tools=tools,
        backend=_build_skills_backend(),
        skills=[DEEPAGENTS_SKILL_SOURCE],
        system_prompt=_research_system_prompt(tool_budget),
        response_format=ResearchReport,
    )


def _build_react_fallback(llm: BaseChatModel, tools: list, tool_budget: int):
    from langgraph.prebuilt import create_react_agent

    from src.core.prompt_builder import PromptBuilder

    _log.warning(
        "deepagents_unavailable",
        message="deepagents requires Python 3.11+ — using LangGraph ReAct fallback with full skill body",
    )
    system_prompt, _ = PromptBuilder.build_system_only(SKILL_DIR)
    return create_react_agent(
        llm,
        tools,
        prompt=system_prompt + f"\n\nMax {tool_budget} tool iterations.",
    )


def build_research_agent(llm: BaseChatModel, tools: list, tool_budget: int):
    try:
        import deepagents  # noqa: F401

        agent = _build_deep_agent(llm, tools, tool_budget)
        _log.info("harness", type="deepagents", skills_source=DEEPAGENTS_SKILL_SOURCE)
        return agent, "deepagents"
    except ImportError:
        return _build_react_fallback(llm, tools, tool_budget), "react_fallback"


def resolve_research_skill(ticket: JiraTicket) -> tuple[Skill | None, str]:
    """Select ticket-type supplemental skill (CLI reporting / ReAct fallback)."""
    return select_research_skill(ticket, SKILLS_ROOT)


def _format_file_hints(repo_path: str, anchors: list[str], repo_profile: RepoProfile | None) -> str:
    ranked = rank_candidate_files(repo_path, anchors, repo_profile)
    if not ranked:
        return ""
    lines = [f"- {path} (score={score:.0f}, {reason})" for path, score, reason in ranked[:5]]
    return "\n".join(lines)


def _is_mock_security_ticket(ticket: JiraTicket) -> bool:
    history = ticket.get("ticket_history_summary") or {}
    if history.get("mock"):
        return True
    flags = ticket.get("flags") or []
    return any("mock security" in str(flag).lower() for flag in flags)


def _file_index_summary(repo_path: str) -> dict[str, Any]:
    index = build_file_index(repo_path)
    top_paths = [p["path"] for p in index.get("paths", [])[:30]]
    return {
        "total_files": index.get("total_files", 0),
        "by_extension": index.get("by_extension", {}),
        "sample_paths": top_paths,
        "truncated": index.get("truncated", False),
    }


def build_research_user_message(
    ticket: JiraTicket,
    repo_path: str,
    repo_profile: RepoProfile | None,
    anchors: list[str],
    user_prompt: str,
    memory: ResearchMemoryStore,
    *,
    file_index: dict[str, Any] | None = None,
) -> str:
    """Runtime context only — skills load via deepagents metadata + read_file on demand."""
    enrichment = (ticket.get("ticket_history_summary") or {}).get("security_enrichment", {})
    memory_snippet = memory.to_context_snippet()
    context = {
        "ticket_id": ticket.get("issue_id"),
        "title": ticket.get("title"),
        "module": ticket.get("module"),
        "problem_statement": ticket.get("problem_statement"),
        "labels": ticket.get("labels", []),
        "repo_path": repo_path,
        "repo_profile": dict(repo_profile) if repo_profile else None,
        "anchors": anchors,
        "user_prompt": user_prompt,
        "security_enrichment": enrichment,
        "file_index": file_index or _file_index_summary(repo_path),
    }

    parts: list[str] = [f"## CONTEXT\n{json.dumps(context, indent=2, default=str)}\n"]

    if memory_snippet:
        parts.append(f"\n## RUN MEMORY\n{memory_snippet}")

    if not _is_mock_security_ticket(ticket):
        file_hints = _format_file_hints(repo_path, anchors, repo_profile)
        if file_hints:
            parts.append(f"\n## PRE-RANKED FILE CANDIDATES\n{file_hints}\n")

    if _is_mock_security_ticket(ticket):
        parts.append(
            "\n## MOCK DEMO HINT\n"
            "This is a local mock-security exercise. The vulnerable demo file is "
            "`src/agents/planner/fixtures/demo/UserSearchController.py` (Python, not Java). "
            "Read it, confirm the SQLi pattern, then return the ResearchReport.\n"
        )

    return "".join(parts)


def build_research_prompt(
    ticket: JiraTicket,
    repo_path: str,
    repo_profile: RepoProfile | None,
    anchors: list[str],
    user_prompt: str,
    memory: ResearchMemoryStore,
    *,
    file_index: dict[str, Any] | None = None,
) -> str:
    """Backward-compatible alias — context message without embedded skill body."""
    return build_research_user_message(
        ticket,
        repo_path,
        repo_profile,
        anchors,
        user_prompt,
        memory,
        file_index=file_index,
    )


def load_skill_manifest_safe():
    return load_skill_manifest(SKILL_DIR)


def _resolve_research_report(result: dict[str, Any]) -> ResearchReport:
    structured = result.get("structured_response")
    if structured is not None:
        if isinstance(structured, ResearchReport):
            return structured
        return ResearchReport.model_validate(structured)
    raw = _extract_final_text(result)
    return parse_llm_json(raw, ResearchReport)


def _mock_security_fallback_report(ticket: JiraTicket) -> ResearchReport:
    """Last-resort report so mock-security demos can proceed when the agent loops."""
    demo_path = "src/agents/planner/fixtures/demo/UserSearchController.py"
    return ResearchReport(
        ticket_id=ticket.get("issue_id") or "SEC-4421",
        research_summary=(
            "Research agent hit recursion limit; using mock-security fallback. "
            f"Inspect {demo_path} for the intentional SQL concatenation demo."
        ),
        hypotheses=[
            {
                "rank": 1,
                "hypothesis": "SQL injection via unparameterized query string in demo controller",
                "evidence_strength": "moderate",
                "evidence": [f"Mock demo target: {demo_path}"],
            }
        ],
        affected_files=[
            {
                "path": demo_path,
                "reason": "Mock security demo fixture (fallback injection)",
                "evidence_strength": "moderate",
            }
        ],
        affected_modules=["auth"],
        module_blast_radius=[],
        architectural_risk="high",
        issue_type_assessment="code_bug",
        flags=["research_recursion_limit_fallback"],
    )


def _extract_final_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        raise ValueError("Research agent returned no messages")

    last = messages[-1]
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = llm_content(msg).strip()
            if text:
                return text
    if isinstance(last, AIMessage):
        return llm_content(last)
    if isinstance(last, tuple) and len(last) >= 2:
        return str(last[1])
    content = getattr(last, "content", None)
    if content:
        return str(content)
    return str(last)


def run_research(
    ticket: JiraTicket,
    repo_path: str,
    *,
    user_prompt: str = "",
    repo_profile: RepoProfile | None = None,
    anchors: list[str] | None = None,
    llm: BaseChatModel | None = None,
    memory: ResearchMemoryStore | None = None,
) -> ResearchReport:
    """Run Research Deep Agent loop; internal tool iterations are not exposed to LangGraph."""
    configure_logging()
    from src.core.llm import TaskComplexity, get_llm
    from src.agents.research.tools.file_discovery import extract_anchors

    memory = memory or InMemoryResearchMemory()
    anchors = anchors if anchors is not None else extract_anchors(ticket, user_prompt)
    llm = llm or get_llm(TaskComplexity.HIGH)

    manifest = load_skill_manifest_safe()
    thread_id = ticket.get("issue_id") or "research"
    init_budget("research", thread_id, manifest.tool_budget)

    file_index = _file_index_summary(repo_path)
    memory.record("research_start", {"ticket_id": ticket.get("issue_id"), "repo_path": repo_path})
    _log.info(
        "start",
        ticket_id=ticket.get("issue_id"),
        repo_path=repo_path,
        anchors=anchors[:10],
        tool_budget=manifest.tool_budget,
        recursion_limit=manifest.recursion_limit,
        skills_mode="deepagents_progressive",
    )

    registry, tools = build_research_tool_registry(repo_path)
    user_message = build_research_user_message(
        ticket,
        repo_path,
        repo_profile,
        anchors,
        user_prompt,
        memory,
        file_index=file_index,
    )

    agent, harness = build_research_agent(llm, tools, manifest.tool_budget)
    # LangGraph counts one step per model/tools node; deepagents adds skill-load steps.
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
        report = _resolve_research_report(result)
        memory.record(
            "agent_complete",
            {"response_preview": report.research_summary[:500]},
        )
        _log.info("complete", ticket_id=report.ticket_id, harness=harness)
        return report
    except Exception as e:
        from langgraph.errors import GraphRecursionError

        if isinstance(e, GraphRecursionError) and _is_mock_security_ticket(ticket):
            _log.warning(
                "recursion_limit_fallback",
                ticket_id=ticket.get("issue_id"),
                harness=harness,
            )
            memory.record("agent_fallback", {"reason": "recursion_limit", "harness": harness})
            return _mock_security_fallback_report(ticket)
        memory.record("agent_error", {"error": str(e), "harness": harness, "stage": stage})
        _log.exception("failed", error=str(e), harness=harness, stage=stage)
        raise RuntimeError(
            f"Research agent failed for ticket={ticket.get('issue_id')!r} repo={repo_path!r}: {e}"
        ) from e
    finally:
        registry.dispose()
