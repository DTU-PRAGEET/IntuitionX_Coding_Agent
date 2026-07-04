"""Progressive skill loader — returns skill body; middleware moves it to system prompt."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.core.logging import get_logger
from src.core.skill_manager import default_skills_root, get_skill_registry, load_skill_body, module_skills_dir

_log = get_logger("load_skill_tool")

SKILLS_ROOT = default_skills_root()
RESEARCH_SKILL_DIR = module_skills_dir("research", SKILLS_ROOT)

# Common LLM aliases / stale vulnerability_category values → canonical skill stems.
_SKILL_ALIASES: dict[str, str] = {
    "rce": "injection",
    "remote-code-execution": "injection",
    "remote_code_execution": "injection",
    "sqli": "injection",
    "sql-injection": "injection",
    "xss": "injection",
    "secrets": "cryptographic-failures",
    "secret": "cryptographic-failures",
    "hardcoded-secret": "cryptographic-failures",
    "hardcoded-secrets": "cryptographic-failures",
    "hardcoded-credentials": "cryptographic-failures",
    "security-misconfiguration": "security-misconfiguration",
    "misconfiguration": "security-misconfiguration",
}


class LoadSkillInput(BaseModel):
    """LangChain tool args for progressive security/domain skill loading.
    Resolved by `load_skill` tool via SkillRegistry in `agents/research/tools/load_skill.py`."""
    skill: str = Field(
        description=(
            "Skill name or virtual path, e.g. research, injection, "
            "/security/injection.md, security/injection.md"
        )
    )


def _normalize_skill_key(skill: str) -> str:
    """Normalize Skill Key for this module."""
    key = skill.strip().strip("/")
    if not key:
        raise ValueError("skill must be non-empty")
    if key.startswith("security/"):
        key = key[len("security/") :]
    if key.endswith(".md"):
        key = Path(key).stem
    return _SKILL_ALIASES.get(key.lower(), key)


def _resolve_skill_path(skill: str) -> Path:
    """Resolve Skill Path for this module."""
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

    available = ", ".join(
        sorted(
            {
                s.name
                for s in get_skill_registry().all_skills()
                if s.module == "security"
            }
        )
    )
    raise ValueError(
        f"Unknown skill {skill!r}. Pick from the Skills System section, e.g. "
        f"'injection' or '/security/injection.md'. Available security skills: {available}"
    )


def _load_skill_body(skill: str) -> tuple[str, str]:
    """Load Skill Body for this module."""
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
        """Load skill."""
        try:
            key, body = _load_skill_body(skill)
        except ValueError as exc:
            _log.warning("load_skill_failed", skill=skill, error=str(exc))
            return f"load_skill error: {exc}"
        _log.info("load_skill", skill=key, chars=len(body))
        return body

    return StructuredTool.from_function(
        func=load_skill,
        name="load_skill",
        description="Load domain skill guidance. Args: skill (name or path). Call once before hypotheses.",
        args_schema=LoadSkillInput,
    )
