"""Research SubAgent — adapts AgentContext to ResearchAgent (milestone 1.3).

M1.3 ORCHESTRATION STACK COMMENTED OUT — see docs/cleanup-audit.md
Uncomment the block below to re-enable.
"""

# M1.3 ORCHESTRATION STACK COMMENTED OUT — see docs/cleanup-audit.md
# Uncomment the block below to re-enable.
#
# # Research SubAgent — adapts AgentContext to ResearchAgent (milestone 1.3).
#
# from __future__ import annotations
#
# from pathlib import Path
#
# from src.agents.research.memory import InMemoryResearchMemory
# from src.agents.research.tools.file_discovery import extract_anchors
# from src.agents.research.tools.repo_profile import profile_repository
# from src.core.agent_context import AgentContext
# from src.core.logging import get_logger
# from src.core.state import JiraTicket
# from src.core.sub_agent import SubAgent
# from src.agents.research.research_agent import ResearchAgent
# from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway
# from src.integrations.cursor.research_runner import report_to_research_output
#
# _log = get_logger("research_sub_agent")
#
#
# class CursorResearchSubAgent(SubAgent):
#     """Thin adapter — delegates to ResearchAgent.run() via CursorGateway search."""
#
#     name = "research"
#
#     def __init__(
#         self,
#         *,
#         repo_path: str | None = None,
#         dry_run: bool = False,
#         gateway: CursorGateway | None = None,
#     ):
#         """Initialize CursorResearchSubAgent with the provided arguments."""
#         self._repo_path = repo_path
#         self._dry_run = dry_run
#         self._gateway = gateway
#
#     def run(self, context: AgentContext) -> AgentContext:
#         """Run for CursorResearchSubAgent."""
#         ticket: JiraTicket = context.metadata.get("jira_ticket") or {}
#         repo_path = self._repo_path or context.metadata.get("repo_path")
#         user_prompt = context.metadata.get("user_prompt") or ""
#
#         if not repo_path:
#             output = {
#                 "ticket_id": context.metadata.get("ticket_id", "UNKNOWN"),
#                 "research_summary": "No repository path available for Cursor research",
#                 "hypotheses": [],
#                 "affected_files": [],
#                 "affected_modules": [],
#                 "module_blast_radius": [],
#                 "architectural_risk": "low",
#                 "issue_type_assessment": "unknown",
#                 "flags": ["missing_repo_path"],
#             }
#             return context.with_phase_output("research", output)
#
#         repo_path = str(Path(repo_path).resolve())
#         memory = InMemoryResearchMemory()
#         anchors = extract_anchors(ticket, user_prompt)
#         repo_profile = profile_repository(repo_path) if Path(repo_path).exists() else None
#
#         gateway = self._gateway
#         if gateway is None:
#             gateway = get_cursor_gateway(Path(repo_path), dry_run=self._dry_run)
#
#         _log.info(
#             "research_sub_agent_start",
#             ticket_id=ticket.get("issue_id"),
#             repo_path=repo_path,
#             dry_run=self._dry_run,
#             anchors=anchors[:10],
#         )
#         report = ResearchAgent(repo_path, gateway=gateway, dry_run=self._dry_run).run(
#             ticket,
#             user_prompt=user_prompt,
#             repo_profile=repo_profile,
#             anchors=anchors,
#             memory=memory,
#         )
#         output = dict(report_to_research_output(report))
#         return context.with_phase_output("research", output)
