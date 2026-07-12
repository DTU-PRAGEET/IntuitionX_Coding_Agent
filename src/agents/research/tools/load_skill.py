"""Progressive skill loader — returns skill body; middleware moves it to system prompt."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.core.logging import get_logger
from src.core.skill_manager import (
    default_skills_root,
    get_skill_registry,
    load_reference,
    load_skill_body,
    module_skills_dir,
)

_log = get_logger("load_skill_tool")

SKILLS_ROOT = default_skills_root()
RESEARCH_SKILL_DIR = module_skills_dir("research", SKILLS_ROOT)

# Research reference stems loadable on demand via load_skill (skills/research/references/).
_RESEARCH_REFERENCE_STEMS = frozenset({"general_patterns", "security_patterns"})

# Bundled with load_skill("research") so tool decision tree is available without a second call.
_RESEARCH_BUNDLED_REFERENCES = ("general_patterns",)

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
            "Skill name or virtual path, e.g. research, general_patterns, security_patterns, "
            "injection, /security/injection.md"
        )
    )


def _normalize_skill_key(skill: str) -> str:
    """Normalize Skill Key for this module."""
    key = skill.strip().strip("/")
    if not key:
        raise ValueError("skill must be non-empty")
    if key.startswith("references/"):
        key = key[len("references/") :]
    if key.startswith("security/"):
        key = key[len("security/") :]
    if key.endswith(".md"):
        key = Path(key).stem
    return _SKILL_ALIASES.get(key.lower(), key)


def _read_skill_file(path: Path) -> str:
    """Return markdown body from a skill or reference file."""
    if path.parent == RESEARCH_SKILL_DIR and path.name == "research.md":
        return load_skill_body(RESEARCH_SKILL_DIR)
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        parts = text.split("---", 2)
        return parts[2].lstrip("\n") if len(parts) >= 3 else text
    return text


def _append_reference_block(body: str, stem: str, ref_body: str) -> str:
    """Append a REFERENCE section to a skill body."""
    return f"{body}\n\n## REFERENCE: {stem}\n\n{ref_body.strip()}"


def _bundle_research_references(body: str, stems: tuple[str, ...]) -> str:
    """Attach on-demand research references bundled with load_skill('research')."""
    combined = body
    for stem in stems:
        try:
            ref_body = load_reference(RESEARCH_SKILL_DIR, stem)
        except ValueError:
            continue
        combined = _append_reference_block(combined, stem, ref_body)
    return combined


def _resolve_skill_path(skill: str) -> Path:
    """Resolve Skill Path for this module."""
    key = _normalize_skill_key(skill)
    root = SKILLS_ROOT.resolve()

    if key in _RESEARCH_REFERENCE_STEMS:
        ref_path = RESEARCH_SKILL_DIR / "references" / f"{key}.md"
        if ref_path.is_file():
            return ref_path

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

    ref_names = ", ".join(sorted(_RESEARCH_REFERENCE_STEMS))
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
        f"'research', '{ref_names}', or 'injection'. "
        f"Available security skills: {available}"
    )


def _load_skill_body(skill: str) -> tuple[str, str]:
    """Load Skill Body for this module."""
    key = _normalize_skill_key(skill)
    path = _resolve_skill_path(skill)
    body = _read_skill_file(path).strip()

    if not body:
        raise ValueError(f"Skill file is empty: {path}")

    if key == "research":
        body = _bundle_research_references(body, _RESEARCH_BUNDLED_REFERENCES)

    return key, body


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
        description=(
            "Load full skill guidance by name when ticket requirements or repo evidence warrant it. "
            "Orchestrator suggestions do NOT restrict which skills you may load — browse the catalog "
            "and load every skill needed (zero, one, or many). "
            "Research references: general_patterns (tool/evidence rules; bundled with research), "
            "security_patterns (security tickets). "
            "Args: skill (catalog name)."
        ),
        args_schema=LoadSkillInput,
    )
