"""Progressive skill loader — returns skill body; middleware moves it to system prompt."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.core.logging import get_logger
from src.core.skill_registry import default_skills_root, module_skills_dir
from src.core.skills_loader import load_skill_body

_log = get_logger("load_skill_tool")

SKILLS_ROOT = default_skills_root()
RESEARCH_SKILL_DIR = module_skills_dir("research", SKILLS_ROOT)


class LoadSkillInput(BaseModel):
    skill: str = Field(
        description=(
            "Skill name or virtual path, e.g. research, injection, "
            "/security/injection.md, security/injection.md"
        )
    )


def _normalize_skill_key(skill: str) -> str:
    key = skill.strip().strip("/")
    if not key:
        raise ValueError("skill must be non-empty")
    return key


def _resolve_skill_path(skill: str) -> Path:
    key = _normalize_skill_key(skill)
    root = SKILLS_ROOT.resolve()

    if key == "research":
        return RESEARCH_SKILL_DIR / "research.md"

    candidates: list[Path] = []
    if key.endswith(".md"):
        candidates.append(root / key)
        candidates.append(root / "security" / Path(key).name)
    else:
        candidates.append(root / "security" / f"{key}.md")
        candidates.append(root / key / "SKILL.md")
        candidates.append(root / key / f"{key}.md")

    for path in candidates:
        if path.is_file():
            return path

    raise ValueError(
        f"Unknown skill {skill!r}. Use a name like 'injection' or path like "
        "'/security/injection.md' from the Skills System section."
    )


def _load_skill_body(skill: str) -> tuple[str, str]:
    key = _normalize_skill_key(skill)
    path = _resolve_skill_path(skill)

    if path.parent == RESEARCH_SKILL_DIR and path.name == "research.md":
        body = load_skill_body(RESEARCH_SKILL_DIR)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.startswith("---"):
            parts = text.split("---", 2)
            body = parts[2].lstrip("\n") if len(parts) >= 3 else text
        else:
            body = text

    if not body.strip():
        raise ValueError(f"Skill file is empty: {path}")
    return key, body.strip()


def make_load_skill_tool() -> StructuredTool:
    """Factory — lightweight progressive skill loader."""

    def load_skill(skill: str) -> str:
        key, body = _load_skill_body(skill)
        _log.info("load_skill", skill=key, chars=len(body))
        return body

    return StructuredTool.from_function(
        func=load_skill,
        name="load_skill",
        description="Load domain skill guidance. Args: skill (name or path). Call once before hypotheses.",
        args_schema=LoadSkillInput,
    )
