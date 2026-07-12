"""SubAgent abstract base — milestone 1.2 orchestration contract.

M1.3 ORCHESTRATION STACK COMMENTED OUT — see docs/cleanup-audit.md
Uncomment the block below to re-enable.
"""

# M1.3 ORCHESTRATION STACK COMMENTED OUT — see docs/cleanup-audit.md
# Uncomment the block below to re-enable.
#
# """SubAgent abstract base — milestone 1.2 orchestration contract."""
#
# from __future__ import annotations
#
# from abc import ABC, abstractmethod
#
# from src.core.agent_context import AgentContext
#
#
# class SubAgent(ABC):
#     """Each pipeline phase implements run(context) -> context with phase output in metadata."""
#
#     name: str
#
#     @abstractmethod
#     def run(self, context: AgentContext) -> AgentContext:
#         """Execute one agent phase and attach structured output to context.metadata."""
