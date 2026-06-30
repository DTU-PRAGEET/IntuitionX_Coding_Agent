"""Research Deep Agent runner — LangChain deepagents with ReAct fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from src.agents.research.memory import InMemoryResearchMemory, ResearchMemoryStore
from src.agents.research.middleware import (
    RESEARCH_EXCLUDED_DEEPAGENT_TOOLS,
    ProgressiveSkillMiddleware,
    ResearchToolFilterMiddleware,
)
from src.agents.research.run_context import ResearchRunContext
from src.agents.research.tools.builder import build_research_tool_registry
from src.agents.research.tools.repo_profile import bootstrap_repo_context
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import llm_content, parse_llm_json
from src.core.skill_scripts.budget import init_budget
from src.core.token_usage import record_messages_usage
from src.core.schemas import ResearchReport, Skill
from src.core.skill_registry import (
    default_skills_root,
    get_skill_registry,
    module_skills_dir,
)
from src.core.skills_loader import load_skill_manifest, select_research_skill
from src.core.state import JiraTicket, RepoProfile
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway

_log = get_logger("research_deep")

SKILLS_ROOT = default_skills_root()
SKILL_DIR = module_skills_dir("research", SKILLS_ROOT)

_SKILL_POINTER_DESC_MAX = 200


def _register_research_harness_profile(tool_budget: int, llm: BaseChatModel) -> None:
    """Register provider/model harness profile so deepagents exclusions actually apply."""
    try:
        from deepagents import (
            GeneralPurposeSubagentProfile,
            HarnessProfile,
            register_harness_profile,
        )
        from deepagents._models import get_model_identifier, get_model_provider
    except ImportError:
        return

    profile = HarnessProfile(
        excluded_tools=RESEARCH_EXCLUDED_DEEPAGENT_TOOLS,
        excluded_middleware=frozenset({"TodoListMiddleware"}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        system_prompt_suffix=_harness_workflow_suffix(tool_budget),
    )

    # Provider-wide default (Sprinklr ChatOpenAI resolves as provider=openai).
    register_harness_profile("openai", profile)

    provider = get_model_provider(llm)
    identifier = get_model_identifier(llm)
    if provider and identifier and ":" not in identifier:
        register_harness_profile(f"{provider}:{identifier}", profile)


def _harness_workflow_suffix(tool_budget: int) -> str:
    return (
        "Begin every investigation with ONE cursor_search call to locate relevant files, "
        "then use read, ls, and bash to inspect the 2-3 files it surfaces. "
        "cursor_search runs a limited, expensive Cursor agent — call it a second time ONLY "
        "if the codebase is large and the first sweep left the location unclear; "
        "never more than 2 cursor_search calls total. Skip cursor_search and read directly "
        "when anchors already name exact file paths. Do not exceed 8 read/ls/bash calls — "
        "emit the ResearchReport once you have enough evidence. "
        "Call load_skill once on research plus the most relevant security skill from "
        "the Skills System section before forming hypotheses. "
        f"Max {tool_budget} tool iterations."
    )


def _research_system_prompt(tool_budget: int) -> str:
    return (
        "Investigate the Jira ticket using progressive skills. "
        "First call load_skill for `research` and the most relevant security skill "
        "listed in the user message, then run one cursor_search to locate relevant files, "
        "then use read/ls/bash to inspect the 2-3 files it surfaces; call cursor_search "
        "again only if the codebase is large and still unclear "
        "(max 2 cursor_search calls total, max 8 read/ls/bash calls total). "
        f"Hard limit: {tool_budget} tool calls. "
        "Return a complete ResearchReport JSON when completion criteria are met."
    )


def _build_deep_agent(
    llm: BaseChatModel,
    tools: list,
    tool_budget: int,
):
    """Create the research-only Deep Agent."""
    from deepagents import create_deep_agent

    _register_research_harness_profile(tool_budget, llm)
    run_context = ResearchRunContext()
    return create_deep_agent(
        model=llm,
        tools=tools,
        middleware=[
            ProgressiveSkillMiddleware(run_context),
            ResearchToolFilterMiddleware(run_context),
        ],
        subagents=[],
        system_prompt=_research_system_prompt(tool_budget),
        response_format=ResearchReport,
    )


def _build_react_fallback(
    llm: BaseChatModel,
    tools: list,
    tool_budget: int,
):
    """LangGraph ReAct fallback when deepagents is unavailable."""
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


def build_research_agent(
    llm: BaseChatModel,
    tools: list,
    tool_budget: int,
):
    try:
        import deepagents  # noqa: F401

        agent = _build_deep_agent(llm, tools, tool_budget)
        _log.info("harness", type="deepagents", research_only=True)
        return agent, "deepagents"
    except ImportError:
        return _build_react_fallback(llm, tools, tool_budget), "react_fallback"


def resolve_research_skill(ticket: JiraTicket) -> tuple[Skill | None, str]:
    return select_research_skill(ticket, SKILL_DIR)


_SECURITY_ENRICHMENT_KEYS = (
    "severity",
    "vulnerability_type",
    "repository",
    "target_branch",
)
_ENRICHMENT_DESCRIPTION_MAX = 800


def _compact_repo_profile(repo_profile: RepoProfile | None) -> dict[str, object] | None:
    """Repo facts research needs to navigate — drops root_path and package_roots noise."""
    if not repo_profile:
        return None
    return {
        "repo_type": repo_profile.get("repo_type", "unknown"),
        "modules": list(repo_profile.get("modules") or [])[:8],
    }


def _compact_security_enrichment(enrichment: dict | None) -> dict[str, object]:
    """Keep the security fields that steer the search; drop verbose internal metadata."""
    if not enrichment:
        return {}
    compact: dict[str, object] = {
        key: enrichment[key] for key in _SECURITY_ENRICHMENT_KEYS if enrichment.get(key)
    }
    description = enrichment.get("description")
    if description:
        text = str(description).strip()
        if len(text) > _ENRICHMENT_DESCRIPTION_MAX:
            text = text[: _ENRICHMENT_DESCRIPTION_MAX - 3].rstrip() + "..."
        compact["description"] = text
    flags = enrichment.get("flags")
    if flags:
        compact["flags"] = list(flags)[:5]
    return compact


def _format_domain_skill_pointer(ticket: JiraTicket | None) -> str:
    """Compact skill catalog — bodies load on demand via load_skill tool."""
    if not ticket:
        return ""

    lines = [
        "\n## Skills System",
        "Load skills progressively with load_skill — call ONCE on `research` plus the "
        "most relevant security skill below before forming hypotheses; do not re-load.",
        "- `research` — core research workflow",
    ]

    skills_root = SKILLS_ROOT.resolve()
    for skill in get_skill_registry().select("research", ticket=ticket):
        if skill.module != "security":
            continue
        try:
            rel = Path(skill.source_path).resolve().relative_to(skills_root)
            path = f"/{rel.as_posix()}"
        except ValueError:
            path = skill.source_path
        desc = " ".join((skill.description or "").split())
        if len(desc) > _SKILL_POINTER_DESC_MAX:
            desc = desc[: _SKILL_POINTER_DESC_MAX - 3].rstrip() + "..."
        lines.append(f"- `{skill.name}` or `{path}` — {desc}")

    if len(lines) <= 3:
        return ""
    return "\n".join(lines) + "\n"


def build_research_user_message(
    ticket: JiraTicket,
    repo_path: str,
    repo_profile: RepoProfile | None,
    anchors: list[str],
    user_prompt: str,
    memory: ResearchMemoryStore,
) -> str:
    """Runtime ticket/repo context — skill bodies load via load_skill on demand."""
    enrichment = (ticket.get("ticket_history_summary") or {}).get("security_enrichment", {})
    memory_snippet = memory.to_context_snippet()
    resolved_repo = str(Path(repo_path).resolve())
    bootstrap = bootstrap_repo_context(resolved_repo)
    context = {
        "ticket_id": ticket.get("issue_id"),
        "title": ticket.get("title"),
        "module": ticket.get("module"),
        "problem_statement": ticket.get("problem_statement"),
        "labels": ticket.get("labels", []),
        "vulnerability_category": ticket.get("vulnerability_category") or "",
        "repo_path": resolved_repo,
        "repo_bootstrap": bootstrap,
        "repo_profile": _compact_repo_profile(repo_profile),
        "anchors": anchors,
        "user_prompt": user_prompt,
        "security_enrichment": _compact_security_enrichment(enrichment),
    }

    parts: list[str] = [f"## CONTEXT\n{json.dumps(context, indent=2, default=str)}\n"]

    if memory_snippet:
        parts.append(f"\n## RUN MEMORY\n{memory_snippet}")

    domain_skills = _format_domain_skill_pointer(ticket)
    if domain_skills:
        parts.append(domain_skills)

    return "".join(parts)


def build_research_prompt(
    ticket: JiraTicket,
    repo_path: str,
    repo_profile: RepoProfile | None,
    anchors: list[str],
    user_prompt: str,
    memory: ResearchMemoryStore,
) -> str:
    return build_research_user_message(
        ticket, repo_path, repo_profile, anchors, user_prompt, memory
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
    gateway: CursorGateway | None = None,
    dry_run: bool = False,
) -> ResearchReport:
    """Backward-compatible alias for ResearchAgent.run()."""
    from src.agents.research.research_agent import ResearchAgent

    agent = ResearchAgent(repo_path, gateway=gateway, dry_run=dry_run)
    return agent.run(
        ticket,
        user_prompt=user_prompt,
        repo_profile=repo_profile,
        anchors=anchors,
        llm=llm,
        memory=memory,
    )
