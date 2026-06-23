"""Load agent skills from SKILL.md with progressive disclosure."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from src.core.logging import get_logger
from src.core.schemas import Skill, SkillManifest
from src.core.state import JiraTicket

_log = get_logger("skills_loader")

_SKILL_MD = "SKILL.md"
_LEGACY_SCRIPTS = "scripts.md"

_SECURITY_LABELS = frozenset({"security", "cve", "vulnerability"})
_REQUIRED_KEYS = ("script_name", "script_description", "script_content")
_KEY_LINE = re.compile(r"^(script_name|script_description|script_content):\s*(.*)$")

# Research reference selection: (predicate, reference stem, reason)
_RESEARCH_REFERENCE_RULES: list[tuple[Callable[[JiraTicket], bool], str, str]] = [
    (lambda t: _labels_match_security(t), "security_patterns", "label_match"),
    (lambda t: _issue_type_is_security(t), "security_patterns", "issue_type_security"),
    (lambda t: _has_cve_ids(t), "security_patterns", "cve_present"),
]


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter between --- markers."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm_block, body = parts[1], parts[2]
    fields: dict[str, str] = {}
    current_key: str | None = None
    block_lines: list[str] = []

    for line in fm_block.splitlines():
        if line.strip() == "" and current_key and block_lines:
            continue
        if not line.startswith(" ") and ":" in line:
            if current_key and block_lines:
                fields[current_key] = "\n".join(block_lines).strip()
                block_lines = []
            key, _, value = line.partition(":")
            current_key = key.strip()
            value = value.strip()
            if value == ">":
                block_lines = []
            elif value == "|":
                block_lines = []
            elif value:
                fields[current_key] = value
                current_key = None
        elif current_key is not None:
            block_lines.append(line.strip() if line.startswith(" ") else line)

    if current_key and block_lines:
        fields[current_key] = "\n".join(block_lines).strip()

    return fields, body.lstrip("\n")


def _skill_md_path(skills_dir: Path) -> Path | None:
    path = skills_dir / _SKILL_MD
    return path if path.is_file() else None


def _resolve_skill_source(skills_dir: Path) -> tuple[Path, bool]:
    """Return (path, is_skill_md). Falls back to scripts.md with deprecation."""
    skill_path = _skill_md_path(skills_dir)
    if skill_path is not None:
        return skill_path, True
    legacy = skills_dir / _LEGACY_SCRIPTS
    if legacy.is_file():
        _log.warning("deprecated_skill_source", path=str(legacy), use=_SKILL_MD)
        return legacy, False
    raise ValueError(f"No SKILL.md or scripts.md in {skills_dir}")


def load_skill_manifest(skills_dir: Path) -> SkillManifest:
    """Level 1 — frontmatter only."""
    path, is_skill_md = _resolve_skill_source(skills_dir)
    text = path.read_text(encoding="utf-8")
    if not is_skill_md:
        return SkillManifest(
            name=skills_dir.parent.name,
            description=f"Legacy skills from {path.name}",
            tool_budget=10,
            recursion_limit=5,
            source_path=str(path.resolve()),
        )
    fields, _ = _parse_frontmatter(text)
    name = fields.get("name", skills_dir.parent.name)
    description = fields.get("description", "").strip()
    tool_budget = int(fields.get("tool_budget", "10"))
    recursion_limit = int(fields.get("recursion_limit", str(max(1, tool_budget // 2))))
    return SkillManifest(
        name=name,
        description=description,
        tool_budget=tool_budget,
        recursion_limit=recursion_limit,
        source_path=str(path.resolve()),
    )


def load_skill_body(skills_dir: Path) -> str:
    """Level 2 — markdown body without frontmatter."""
    path, is_skill_md = _resolve_skill_source(skills_dir)
    text = path.read_text(encoding="utf-8")
    if is_skill_md:
        _, body = _parse_frontmatter(text)
        return body
    return text


def load_reference(skills_dir: Path, ref_name: str) -> str:
    """Level 3 — load a references/*.md file by stem name."""
    ref_path = skills_dir / "references" / f"{ref_name}.md"
    if not ref_path.is_file():
        raise ValueError(f"Reference not found: {ref_path}")
    return ref_path.read_text(encoding="utf-8")


def select_references(ticket: JiraTicket, skills_dir: Path, agent: str) -> list[Path]:
    """Return reference paths to load for this ticket/agent."""
    if agent != "research":
        return []
    refs_dirs = [
        skills_dir / "references",
        skills_dir / "research" / "references",
    ]
    refs_dir = next((d for d in refs_dirs if d.is_dir()), None)
    if refs_dir is None:
        return _legacy_research_skill_as_reference(ticket, skills_dir)

    selected: list[Path] = []
    seen: set[str] = set()
    for predicate, stem, reason in _RESEARCH_REFERENCE_RULES:
        if predicate(ticket):
            ref = refs_dir / f"{stem}.md"
            if ref.is_file() and stem not in seen:
                _log.info("reference_selected", agent=agent, ref=stem, reason=reason)
                selected.append(ref)
                seen.add(stem)
    return selected


def _legacy_research_skill_as_reference(ticket: JiraTicket, skills_dir: Path) -> list[Path]:
    """Backward compat: security.md treated as reference when references/ missing."""
    skill, reason = select_research_skill(ticket, skills_dir)
    if skill is None:
        return []
    path = Path(skill.source_path)
    return [path] if path.is_file() else []


# ── Legacy supplemental skill format (security.md) ───────────────────────────


def _parse_skill_text(text: str, path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _KEY_LINE.match(lines[i])
        if not match:
            i += 1
            continue
        key, value = match.group(1), match.group(2).strip()
        if value == "|":
            i += 1
            block_lines: list[str] = []
            while i < len(lines):
                line = lines[i]
                if _KEY_LINE.match(line):
                    break
                if line.startswith("  ") or line == "":
                    block_lines.append(line[2:] if line.startswith("  ") else "")
                    i += 1
                else:
                    break
            fields[key] = "\n".join(block_lines).rstrip("\n")
        else:
            fields[key] = value
            i += 1
    missing = [k for k in _REQUIRED_KEYS if k not in fields or not fields[k].strip()]
    if missing:
        raise ValueError(
            f"Malformed skill file {path}: missing or empty required field(s): {', '.join(missing)}"
        )
    return fields


def load_skill_file(path: Path) -> Skill:
    """Parse legacy script_name / script_description / script_content skill file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read skill file {path}: {e}") from e
    fields = _parse_skill_text(text, path)
    return Skill(
        name=fields["script_name"],
        description=fields["script_description"],
        content=fields["script_content"],
        source_path=str(path.resolve()),
    )


def load_skills_from_dir(skills_dir: Path) -> dict[str, Skill]:
    """Load legacy *.md supplemental skills except scripts.md and SKILL.md."""
    skills: dict[str, Skill] = {}
    if not skills_dir.is_dir():
        raise ValueError(f"Skills directory does not exist: {skills_dir}")
    for path in sorted(skills_dir.glob("*.md")):
        if path.name in (_LEGACY_SCRIPTS, _SKILL_MD):
            continue
        try:
            skill = load_skill_file(path)
            skills[path.stem] = skill
        except ValueError as e:
            raise ValueError(f"Failed loading skills from {skills_dir}: {e}") from e
    return skills


def _labels_match_security(ticket: JiraTicket) -> bool:
    labels = {label.lower() for label in ticket.get("labels", [])}
    return bool(labels & _SECURITY_LABELS)


def _issue_type_is_security(ticket: JiraTicket) -> bool:
    return (ticket.get("issue_type") or "").lower() == "security"


def _has_cve_ids(ticket: JiraTicket) -> bool:
    enrichment = (ticket.get("ticket_history_summary") or {}).get("security_enrichment") or {}
    cve_ids = enrichment.get("cve_ids") or []
    return bool(cve_ids)


_RESEARCH_SKILL_RULES: list[tuple[Callable[[JiraTicket], bool], str, str]] = [
    (_labels_match_security, "security", "label_match"),
    (_issue_type_is_security, "security", "issue_type_security"),
    (_has_cve_ids, "security", "cve_present"),
]


def select_research_skill(ticket: JiraTicket, skills_dir: Path) -> tuple[Skill | None, str]:
    """Legacy: return supplemental skill from security.md."""
    skills = load_skills_from_dir(skills_dir)
    for predicate, stem, reason in _RESEARCH_SKILL_RULES:
        if predicate(ticket):
            skill = skills.get(stem)
            if skill is None:
                for ref in (
                    skills_dir / "references" / "security_patterns.md",
                    skills_dir / "research" / "references" / "security_patterns.md",
                ):
                    if ref.is_file():
                        content = ref.read_text(encoding="utf-8")
                        return Skill(
                            name="security_research",
                            description="Research strategy for security/CVE tickets",
                            content=content,
                            source_path=str(ref.resolve()),
                        ), reason
                raise ValueError(
                    f"Skill rule matched stem={stem!r} reason={reason!r} "
                    f"but no file {stem}.md in {skills_dir}"
                )
            _log.info("loaded", loaded=stem, reason=reason, path=skill.source_path)
            return skill, reason
    _log.info("loaded", loaded="none", reason="no_skill_match")
    return None, "no_skill_match"
