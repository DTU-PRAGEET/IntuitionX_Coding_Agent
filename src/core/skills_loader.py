"""Load ticket-type skills from markdown and select by deterministic rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from src.core.logging import get_logger
from src.core.schemas import Skill
from src.core.state import JiraTicket

_log = get_logger("skills_loader")

_SECURITY_LABELS = frozenset({"security", "cve", "vulnerability"})
_REQUIRED_KEYS = ("script_name", "script_description", "script_content")
_KEY_LINE = re.compile(r"^(script_name|script_description|script_content):\s*(.*)$")


def _parse_skill_text(text: str, path: Path) -> dict[str, str]:
    """Parse YAML-style skill file with optional block scalar for script_content."""
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
    """Parse script_name / script_description / script_content. Raise ValueError on malformed file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read skill file {path}: {e}") from e
    try:
        fields = _parse_skill_text(text, path)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to parse skill file {path}: {e}") from e
    return Skill(
        name=fields["script_name"],
        description=fields["script_description"],
        content=fields["script_content"],
        source_path=str(path.resolve()),
    )


def load_skills_from_dir(skills_dir: Path) -> dict[str, Skill]:
    """Load all *.md except scripts.md into stem -> Skill map."""
    skills: dict[str, Skill] = {}
    if not skills_dir.is_dir():
        raise ValueError(f"Skills directory does not exist: {skills_dir}")
    for path in sorted(skills_dir.glob("*.md")):
        if path.name == "scripts.md":
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


# Ordered rules: (predicate, skill_stem, reason). First match wins.
_RESEARCH_SKILL_RULES: list[tuple[Callable[[JiraTicket], bool], str, str]] = [
    (_labels_match_security, "security", "label_match"),
    (_issue_type_is_security, "security", "issue_type_security"),
    (_has_cve_ids, "security", "cve_present"),
]


def select_research_skill(ticket: JiraTicket, skills_dir: Path) -> tuple[Skill | None, str]:
    """Return (skill, reason). reason explains why selected or why none."""
    skills = load_skills_from_dir(skills_dir)
    for predicate, stem, reason in _RESEARCH_SKILL_RULES:
        if predicate(ticket):
            skill = skills.get(stem)
            if skill is None:
                raise ValueError(
                    f"Skill rule matched stem={stem!r} reason={reason!r} "
                    f"but no file {stem}.md in {skills_dir}"
                )
            _log.info("loaded", loaded=stem, reason=reason, path=skill.source_path)
            return skill, reason
    _log.info("loaded", loaded="none", reason="no_skill_match")
    return None, "no_skill_match"
