"""Research Deep Agent runner — LangChain deepagents with ReAct fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from src.agents.research.memory import InMemoryResearchMemory, ResearchMemoryStore
from src.agents.research.tools.builder import build_research_tool_registry
from src.agents.research.tools.repo_profile import bootstrap_repo_context
from src.core.logging import configure_logging, get_logger
from src.core.runner_utils import llm_content, parse_llm_json
from src.core.skill_scripts.budget import init_budget
from src.core.token_usage import record_messages_usage
from src.core.schemas import ResearchReport, Skill
from src.core.skill_registry import default_skills_root, module_skills_dir
from src.core.skills_loader import load_skill_manifest, select_research_skill
from src.core.state import JiraTicket, RepoProfile
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway

_log = get_logger("research_deep")

SKILLS_ROOT = default_skills_root()
SKILL_DIR = module_skills_dir("research", SKILLS_ROOT)

DEEPAGENTS_FS_TOOLS = frozenset(
    {"ls", "write_file", "edit_file", "glob", "grep", "execute"}
)
HARNESS_PROFILE_KEY = "sprinklr-research"
DEEPAGENTS_SKILL_SOURCE = "/"


def _register_research_harness_profile(tool_budget: int) -> None:
    """Hide deepagents built-in filesystem tools — repo nav goes through cursor_search."""
    try:
        from deepagents import HarnessProfile, register_harness_profile

        register_harness_profile(
            HARNESS_PROFILE_KEY,
            HarnessProfile(
                excluded_tools=DEEPAGENTS_FS_TOOLS,
                system_prompt_suffix=(
                    "Use cursor_search for ALL repository investigation. "
                    "Do NOT traverse files directly with read/grep/find/ls/bash. "
                    "Use read_file only for loading skill files listed in the Skills System section. "
                    f"Never call write_file or edit_file. Max {tool_budget} tool iterations."
                ),
            ),
        )
    except ImportError:
        pass


def _research_system_prompt(tool_budget: int) -> str:
    return (
        "Investigate the Jira ticket using the research skill and cursor_search. "
        f"Hard limit: {tool_budget} tool calls. "
        "Return a complete ResearchReport JSON when completion criteria are met."
    )


def _build_skills_backend():
    from deepagents.backends import FilesystemBackend

    return FilesystemBackend(root_dir=str(SKILLS_ROOT.resolve()), virtual_mode=True)


def _build_deep_agent(llm: BaseChatModel, tools: list, tool_budget: int):
    """It creates the actual Deep Agent."""
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

# print("**********************************************************")
# print("**********************************************************")
# print("**********************************************************")
def _build_react_fallback(llm: BaseChatModel, tools: list, tool_budget: int):
    """If DeepAgents isn't installed or ImportError, it falls back to create_react_agent(...) using LangGraph."""
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
    return select_research_skill(ticket, SKILL_DIR)


def _is_mock_security_ticket(ticket: JiraTicket) -> bool:
    history = ticket.get("ticket_history_summary") or {}
    if history.get("mock"):
        return True
    flags = ticket.get("flags") or []
    return any("mock security" in str(flag).lower() for flag in flags)


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


def build_research_user_message(
    ticket: JiraTicket,
    repo_path: str,
    repo_profile: RepoProfile | None,
    anchors: list[str],
    user_prompt: str,
    memory: ResearchMemoryStore,
) -> str:
    """Runtime context — skills load via deepagents metadata + read_file on demand."""
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

    if _is_mock_security_ticket(ticket):
        parts.append(
            "\n## MOCK DEMO HINT\n"
            "This is a local mock-security exercise. Use cursor_search to locate the vulnerable "
            "demo file (likely under src/agents/planner/fixtures/demo/). "
            "Confirm the SQLi pattern, then return the ResearchReport.\n"
        )

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


def _mock_security_fallback_report(ticket: JiraTicket) -> ResearchReport:
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
