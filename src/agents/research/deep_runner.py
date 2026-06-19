"""Research Deep Agent runner — LangChain deepagents with ReAct fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from src.agents.research.memory import InMemoryResearchMemory, ResearchMemoryStore
from src.agents.research.tools.builder import build_research_tool_registry
from src.core.config import settings
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import llm_content, parse_llm_json
from src.core.schemas import ResearchReport, Skill
from src.core.skills_loader import select_research_skill
from src.core.state import JiraTicket, RepoProfile

_log = get_logger("research_deep")

SKILLS_DIR = Path(__file__).parent / "skills"

DEEPAGENTS_FS_TOOLS = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}
)
HARNESS_PROFILE_KEY = "sprinklr-research"


def _register_research_harness_profile() -> None:
    """Hide deepagents built-in filesystem tools — we use src/tools primitives."""
    try:
        from deepagents import HarnessProfile, register_harness_profile

        register_harness_profile(
            HARNESS_PROFILE_KEY,
            HarnessProfile(
                excluded_tools=DEEPAGENTS_FS_TOOLS,
                system_prompt_suffix=(
                    "Use ONLY the provided read/grep/find/bash/ls tools for repository work. "
                    "Never call write_file or edit_file. Max 5 tool iterations."
                ),
            ),
        )
    except ImportError:
        pass


def _build_deep_agent(llm: BaseChatModel, tools: list, system_prompt: str):
    """Create deep agent when deepagents is installed (Python 3.11+)."""
    from deepagents import create_deep_agent

    _register_research_harness_profile()
    return create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
    )


def _build_react_fallback(llm: BaseChatModel, tools: list):
    from langgraph.prebuilt import create_react_agent

    _log.warning(
        "deepagents_unavailable",
        message="deepagents requires Python 3.11+ — using LangGraph ReAct fallback",
    )
    return create_react_agent(llm, tools)


def build_research_agent(llm: BaseChatModel, tools: list, system_prompt: str):
    try:
        import deepagents  # noqa: F401

        agent = _build_deep_agent(llm, tools, system_prompt)
        _log.info("harness", type="deepagents")
        return agent, "deepagents"
    except ImportError:
        return _build_react_fallback(llm, tools), "react_fallback"


def _load_skills() -> str:
    skills_path = SKILLS_DIR / "scripts.md"
    return skills_path.read_text(encoding="utf-8")


def resolve_research_skill(ticket: JiraTicket) -> tuple[Skill | None, str]:
    """Select ticket-type skill; single source of truth for CLI and prompt building."""
    return select_research_skill(ticket, SKILLS_DIR)


def build_research_prompt(
    ticket: JiraTicket,
    repo_path: str,
    repo_profile: RepoProfile | None,
    anchors: list[str],
    user_prompt: str,
    memory: ResearchMemoryStore,
    ticket_skill: Skill | None = None,
) -> str:
    skills = _load_skills()
    if ticket_skill is None:
        ticket_skill, _ = resolve_research_skill(ticket)
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
    }
    parts = [skills]
    if ticket_skill is not None:
        parts.extend(
            [
                f"\n\n## TICKET-TYPE SKILL: {ticket_skill.name}\n",
                ticket_skill.description,
                "\n\n",
                ticket_skill.content,
            ]
        )
    parts.extend(
        [
            "\n## RESEARCH CONTEXT (JSON)\n",
            json.dumps(context, indent=2, default=str),
        ]
    )
    if memory_snippet:
        parts.extend(["\n## RUN MEMORY\n", memory_snippet])
    parts.append(
        "\n\nInvestigate using tools (max 5 iterations). "
        "Return ONLY a valid ResearchReport JSON object as your final message."
    )
    return "".join(parts)


def _extract_final_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        raise ValueError("Research agent returned no messages")
    last = messages[-1]
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

    memory.record("research_start", {"ticket_id": ticket.get("issue_id"), "repo_path": repo_path})
    _log.info(
        "start",
        ticket_id=ticket.get("issue_id"),
        repo_path=repo_path,
        anchors=anchors[:10],
    )

    registry, tools = build_research_tool_registry(repo_path)
    system_prompt = _load_skills()
    user_message = build_research_prompt(
        ticket, repo_path, repo_profile, anchors, user_prompt, memory
    )

    agent, harness = build_research_agent(llm, tools, system_prompt)
    recursion_limit = max(settings.max_research_iterations * 4, 12)

    try:
        memory.record("agent_invoke", {"harness": harness, "tool_count": len(tools)})
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_message}]},
            config={"recursion_limit": recursion_limit},
        )
        raw = _extract_final_text(result)
        memory.record("agent_complete", {"response_preview": raw[:500]})
        report = parse_llm_json(raw, ResearchReport)
        _log.info("complete", ticket_id=report.ticket_id, harness=harness)
        return report
    except Exception as e:
        memory.record("agent_error", {"error": str(e), "harness": harness})
        _log.error("failed", error=str(e), harness=harness)
        raise RuntimeError(
            f"Research agent failed for ticket={ticket.get('issue_id')!r} repo={repo_path!r}: {e}"
        ) from e
    finally:
        registry.dispose()
