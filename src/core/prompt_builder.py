"""Central prompt assembly with progressive skill disclosure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.schemas import SkillManifest
from src.core.skills_loader import (
    load_reference,
    load_skill_body,
    load_skill_manifest,
    select_references,
)
from src.core.state import JiraTicket


class PromptBuilder:
    """Load SKILL.md body + optional references + runtime context."""

    @staticmethod
    def build(
        agent: str,
        skills_dir: Path,
        context: dict[str, Any],
        *,
        ticket: JiraTicket | None = None,
        include_references: bool = True,
        extra_instructions: str = "",
    ) -> tuple[str, SkillManifest]:
        manifest = load_skill_manifest(skills_dir)
        parts: list[str] = [load_skill_body(skills_dir)]

        if include_references and ticket is not None:
            for ref_path in select_references(ticket, skills_dir, agent):
                stem = ref_path.stem
                try:
                    content = load_reference(skills_dir, stem)
                except ValueError:
                    content = ref_path.read_text(encoding="utf-8")
                parts.append(f"\n\n## REFERENCE: {stem}\n\n{content}")

        if extra_instructions:
            parts.append(f"\n\n{extra_instructions}")

        parts.append(f"\n\n## CONTEXT\n{json.dumps(context, indent=2, default=str)}\n")
        return "".join(parts), manifest

    @staticmethod
    def build_system_only(skills_dir: Path) -> tuple[str, SkillManifest]:
        """Level-2 body only — for research system prompt without duplicating context."""
        manifest = load_skill_manifest(skills_dir)
        return load_skill_body(skills_dir), manifest
