"""Per-run mutable context shared across research middleware."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResearchRunContext:
    """Tracks skills loaded during one research agent invocation."""

    loaded_skills: dict[str, str] = field(default_factory=dict)

    @property
    def has_loaded_skills(self) -> bool:
        return bool(self.loaded_skills)
