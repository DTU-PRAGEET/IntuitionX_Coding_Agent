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
from src.agents.research.middleware import ResearchRunContext
from src.agents.research.tools.builder import build_research_tool_registry
from src.agents.research.tools.repo_profile import bootstrap_repo_context
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import llm_content, parse_llm_json
from src.core.skill_scripts.budget import init_budget
from src.core.token_usage import record_messages_usage
from src.core.schemas import ResearchReport
from src.core.skill_manager import (
    LoadedSkill,
    default_skills_root,
    get_skill_registry,
    load_skill_manifest,
    module_skills_dir,
)
from src.core.state import JiraTicket, RepoProfile
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway

_log = get_logger("research_deep")

SKILLS_ROOT = default_skills_root()
SKILL_DIR = module_skills_dir("research", SKILLS_ROOT)

_SKILL_POINTER_DESC_MAX = 200

_SKILL_SELECTION_GUIDANCE = (
    "Skill selection: orchestrator suggested_security_skill is only a suggestion from initial "
    "ticket analysis — it is NOT a restriction. You must independently decide which skills to "
    "load based on ticket requirements, repository evidence, and files/configurations discovered "
    "during research. You may ignore the suggested skill if irrelevant, load it if useful, or "
    "load additional skills when multiple security concerns exist. Do not treat skill selection as "
    "classification — treat skills as expert tools to consult when needed. Always load every skill "
    "required to produce a complete fix (e.g. committed secrets may need both "
    "cryptographic-failures and security-misconfiguration)."
)


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
    """Harness Workflow Suffix for this module."""
    return (
        "Begin every investigation with ONE cursor_search call to locate relevant files, "
        "then use read, ls, and bash to inspect the 2-3 files it surfaces. "
        "cursor_search runs a limited, expensive Cursor agent — call it a second time ONLY "
        "if the codebase is large and the first sweep left the location unclear; "
        "never more than 2 cursor_search calls total. Skip cursor_search and read directly "
        "when anchors already name exact file paths. Do not exceed 8 read/ls/bash calls — "
        "emit the ResearchReport once you have enough evidence. "
        "Browse the full Skills System catalog — orchestrator hints do NOT restrict your choices. "
        "load_skill when repo evidence or ticket context justifies each skill (zero, one, or many). "
        f"Max {tool_budget} tool iterations."
    )


def _research_system_prompt(tool_budget: int) -> str:
    """Research System Prompt for this module."""
    return (
        "Investigate the Jira ticket using progressive skills. "
        f"{_SKILL_SELECTION_GUIDANCE} "
        "Read the Skills System catalog (name, description, triggers only) — compare every "
        "catalog entry against evidence, not only the orchestrator hint. "
        "Call load_skill(skill_name) for each skill your investigation requires. "
        "Begin with cursor_search (or direct read when paths are named), then inspect surfaced "
        "files with read/ls/bash; call cursor_search again only if the codebase is large and "
        "still unclear (max 2 cursor_search calls, max 8 read/ls/bash calls). "
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

    from src.core.skill_manager import PromptBuilder

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
    """Build research agent."""
    try:
        import deepagents  # noqa: F401

        agent = _build_deep_agent(llm, tools, tool_budget)
        _log.info("harness", type="deepagents", research_only=True)
        return agent, "deepagents"
    except ImportError:
        return _build_react_fallback(llm, tools, tool_budget), "react_fallback"

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


def _format_trigger_summary(skill: LoadedSkill) -> str:
    """Compact trigger summary for the skill catalog."""
    triggers = skill.trigger_conditions or {}
    if not triggers:
        return "none"
    parts: list[str] = []
    for key in ("labels", "issue_types", "languages", "fix_types"):
        val = triggers.get(key)
        if not val:
            continue
        if isinstance(val, list):
            joined = ", ".join(str(v) for v in val[:10])
            if len(val) > 10:
                joined += ", ..."
            parts.append(f"{key}=[{joined}]")
        else:
            parts.append(f"{key}={val}")
    return "; ".join(parts) if parts else "none"


def _format_orchestrator_hints(ticket: JiraTicket | None) -> str:
    """Non-binding orchestrator hints — Research decides which skills to load."""
    if not ticket:
        return ""
    orch = (ticket.get("ticket_history_summary") or {}).get("orchestrator_analysis") or {}
    hints: dict[str, str] = {}
    suggested = orch.get("recommended_security_skill")
    if suggested:
        hints["suggested_security_skill"] = str(suggested)
    category = (ticket.get("vulnerability_category") or "").strip()
    if category:
        hints["vulnerability_category"] = category
    if not hints:
        return ""
    return (
        "\n## Orchestrator hints (suggestions only — NOT restrictions)\n"
        "The values below come from initial ticket triage. They do NOT limit which skills you "
        "may load. Browse the full Skills System catalog and independently select every skill "
        "justified by ticket requirements and repo evidence.\n"
        f"{json.dumps(hints, indent=2)}\n"
    )


def _format_domain_skill_catalog(ticket: JiraTicket | None) -> str:
    """Lightweight skill catalog — full bodies load on demand via load_skill."""
    registry = get_skill_registry()
    security_skills = sorted(
        (
            skill
            for skill in registry.all_skills()
            if skill.module == "security" and registry._matches_agent(skill, "research")
        ),
        key=lambda skill: skill.name,
    )

    lines = [
        "\n## Skills System",
        "Catalog only — skill bodies are NOT included here.",
        _SKILL_SELECTION_GUIDANCE,
        "Scan the full catalog below. Match trigger_conditions and descriptions to ticket context "
        "and repo evidence — do not load only the orchestrator suggested skill.",
        "Call load_skill(skill_name) for each expert guide your investigation needs.",
        "Use exact skill names below — never invent paths from vulnerability_category.",
        "",
        "| skill_name | description | trigger_conditions |",
        "|------------|-------------|--------------------|",
        "| `research` | Core research workflow and completion criteria | always available |",
        "| `general_patterns` | Tool selection and evidence baseline (bundled with research) | load_skill |",
        "| `security_patterns` | Security investigation order and grep patterns | load_skill on security tickets |",
    ]

    for skill in security_skills:
        desc = " ".join((skill.description or "").split())
        if len(desc) > _SKILL_POINTER_DESC_MAX:
            desc = desc[: _SKILL_POINTER_DESC_MAX - 3].rstrip() + "..."
        triggers = _format_trigger_summary(skill)
        lines.append(f"| `{skill.name}` | {desc} | {triggers} |")

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

    hints = _format_orchestrator_hints(ticket)
    if hints:
        parts.append(hints)

    skill_catalog = _format_domain_skill_catalog(ticket)
    if skill_catalog:
        parts.append(skill_catalog)

    return "".join(parts)


def build_research_prompt(
    ticket: JiraTicket,
    repo_path: str,
    repo_profile: RepoProfile | None,
    anchors: list[str],
    user_prompt: str,
    memory: ResearchMemoryStore,
) -> str:
    """Build research prompt."""
    return build_research_user_message(
        ticket, repo_path, repo_profile, anchors, user_prompt, memory
    )


def load_skill_manifest_safe():
    """Load skill manifest safe."""
    return load_skill_manifest(SKILL_DIR)


def _resolve_research_report(result: dict[str, Any]) -> ResearchReport:
    """Resolve Research Report for this module."""
    structured = result.get("structured_response")
    if structured is not None:
        if isinstance(structured, ResearchReport):
            return structured
        return ResearchReport.model_validate(structured)
    raw = _extract_final_text(result)
    return parse_llm_json(raw, ResearchReport)


def _extract_final_text(result: dict[str, Any]) -> str:
    """Extract Final Text for this module."""
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
