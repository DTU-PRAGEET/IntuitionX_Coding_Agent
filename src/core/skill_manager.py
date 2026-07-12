"""Unified skill loading, registry, and prompt assembly (SkillManager)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.core.logging import get_logger
from src.core.schemas import FixPlan, Skill, SkillManifest
from src.core.state import JiraTicket

_log = get_logger("skill_manager")

_SKILL_MD = "SKILL.md"
_LEGACY_SCRIPTS = "scripts.md"

_SECURITY_LABELS = frozenset({"security", "cve", "vulnerability"})
_REQUIRED_KEYS = ("script_name", "script_description", "script_content")
_KEY_LINE = re.compile(r"^(script_name|script_description|script_content):\s*(.*)$")

# Reference selection: (predicate, reference stem, reason) per agent module
_RESEARCH_REFERENCE_RULES: list[tuple[Callable[[JiraTicket], bool], str, str]] = [
    (lambda _t: True, "general_patterns", "default_baseline"),
    (lambda t: _labels_match_security(t), "security_patterns", "label_match"),
    (lambda t: _issue_type_is_security(t), "security_patterns", "issue_type_security"),
    (lambda t: _has_cve_ids(t), "security_patterns", "cve_present"),
]
_PLANNER_REFERENCE_RULES: list[tuple[Callable[[JiraTicket], bool], str, str]] = [
    (lambda _t: True, "plan_schema", "default_schema"),
]
_EXECUTOR_REFERENCE_RULES: list[tuple[Callable[[JiraTicket], bool], str, str]] = [
    (lambda _t: True, "validation_policy", "default_validation"),
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
    """Skill Md Path for this module."""
    path = skills_dir / _SKILL_MD
    return path if path.is_file() else None


def _resolve_skill_source(skills_dir: Path) -> tuple[Path, bool]:
    """Return (path, is_skill_md). Falls back to scripts.md with deprecation."""
    skill_path = _skill_md_path(skills_dir)
    if skill_path is not None:
        return skill_path, True
    module_md = skills_dir / f"{skills_dir.name}.md"
    if module_md.is_file():
        return module_md, True
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


def _select_module_references(
    ticket: JiraTicket,
    skills_dir: Path,
    agent: str,
    rules: list[tuple[Callable[[JiraTicket], bool], str, str]],
) -> list[Path]:
    """Apply reference rules against a module's references/ directory."""
    refs_dirs = [
        skills_dir / "references",
        skills_dir / "research" / "references",
    ]
    refs_dir = next((d for d in refs_dirs if d.is_dir()), None)
    if refs_dir is None:
        return []

    selected: list[Path] = []
    seen: set[str] = set()
    for predicate, stem, reason in rules:
        if predicate(ticket):
            ref = refs_dir / f"{stem}.md"
            if ref.is_file() and stem not in seen:
                _log.info("reference_selected", agent=agent, ref=stem, reason=reason)
                selected.append(ref)
                seen.add(stem)
    return selected


def select_references(
    ticket: JiraTicket | None,
    skills_dir: Path,
    agent: str,
) -> list[Path]:
    """Return reference paths to load for this ticket/agent."""
    effective: JiraTicket = ticket or {}  # type: ignore[assignment]
    if agent == "research":
        refs = _select_module_references(
            effective, skills_dir, agent, _RESEARCH_REFERENCE_RULES
        )
        if refs:
            return refs
        return _legacy_research_skill_as_reference(effective, skills_dir)
    if agent == "planner":
        return _select_module_references(
            effective, skills_dir, agent, _PLANNER_REFERENCE_RULES
        )
    if agent == "executor":
        return _select_module_references(
            effective, skills_dir, agent, _EXECUTOR_REFERENCE_RULES
        )
    return []


def format_reference_sections(skills_dir: Path, ref_paths: list[Path]) -> str:
    """Append REFERENCE blocks for resolved reference paths."""
    parts: list[str] = []
    for ref_path in ref_paths:
        stem = ref_path.stem
        try:
            content = load_reference(skills_dir, stem)
        except ValueError:
            content = ref_path.read_text(encoding="utf-8")
        parts.append(f"\n\n## REFERENCE: {stem}\n\n{content}")
    return "".join(parts)


def append_references_for_agent(
    body: str,
    skills_dir: Path,
    agent: str,
    ticket: JiraTicket | None = None,
) -> str:
    """Load and append reference markdown for an agent module."""
    refs = select_references(ticket, skills_dir, agent)
    if not refs:
        return body
    return body + format_reference_sections(skills_dir, refs)


def _legacy_research_skill_as_reference(ticket: JiraTicket, skills_dir: Path) -> list[Path]:
    """Backward compat: security.md treated as reference when references/ missing."""
    skill, reason = select_research_skill(ticket, skills_dir)
    if skill is None:
        return []
    path = Path(skill.source_path)
    return [path] if path.is_file() else []


# ── Legacy supplemental skill format (security.md) ───────────────────────────


def _parse_skill_text(text: str, path: Path) -> dict[str, str]:
    """Parse Skill Text for this module."""
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
        if path.name.lower() == "readme.md":
            continue
        if path.stem == skills_dir.name:
            continue
        try:
            skill = load_skill_file(path)
            skills[path.stem] = skill
        except ValueError as e:
            raise ValueError(f"Failed loading skills from {skills_dir}: {e}") from e
    return skills


def _labels_match_security(ticket: JiraTicket) -> bool:
    """Labels Match Security for this module."""
    labels = {label.lower() for label in ticket.get("labels", [])}
    return bool(labels & _SECURITY_LABELS)


def _issue_type_is_security(ticket: JiraTicket) -> bool:
    """Issue Type Is Security for this module."""
    return (ticket.get("issue_type") or "").lower() == "security"


def _has_cve_ids(ticket: JiraTicket) -> bool:
    """Has Cve Ids for this module."""
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

_AGENT_MODULE_MAP: dict[str, str] = {
    "orchestrator": "orchestrator",
    "research": "research",
    "planner": "planner",
    "implement": "executor",
    "executor": "executor",
}

_SECURITY_LABELS = frozenset({"security", "cve", "vulnerability", "sast", "sca", "sqli"})


def default_skills_root() -> Path:
    """Repo-root skills/ directory."""
    return Path(__file__).resolve().parents[2] / "skills"


def module_skills_dir(module: str, skills_root: Path | None = None) -> Path:
    """Module Skills Dir for this module."""
    root = skills_root or default_skills_root()
    return root / module


@dataclass
class LoadedSkill:
    """Parsed SKILL.md manifest + sections from `skills/<module>/`.
    Selected by `SkillRegistry.match()` using ticket labels, FixPlan fix_types, and agents."""
    name: str
    module: str
    description: str
    source_path: str
    agents: list[str] = field(default_factory=list)
    fix_types: list[str] = field(default_factory=list)
    trigger_conditions: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    tool_budget: int = 10
    recursion_limit: int = 5

    def section(self, name: str) -> str:
        """Section for LoadedSkill."""
        return self.sections.get(name, "")


def _parse_list_value(raw: str) -> list[str]:
    """Parse List Value for this module."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_trigger_block(fm: dict[str, str]) -> dict[str, Any]:
    """Parse Trigger Block for this module."""
    triggers: dict[str, Any] = {}
    for key in ("labels", "languages", "fix_types", "issue_types"):
        val = fm.get(f"trigger_{key}") or fm.get(key)
        if val:
            triggers[key] = _parse_list_value(val) if isinstance(val, str) else val
    nested = fm.get("trigger_conditions")
    if nested and isinstance(nested, str):
        for line in nested.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                triggers[k.strip()] = _parse_list_value(v)
    return triggers


def _parse_sections(body: str) -> dict[str, str]:
    """Parse Sections for this module."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+)$", line.strip())
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip().lower().replace(" ", "_")
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _coerce_frontmatter(fields: dict[str, str]) -> dict[str, Any]:
    """Coerce Frontmatter for this module."""
    out: dict[str, Any] = dict(fields)
    for key in ("agents", "fix_types"):
        if key in out and isinstance(out[key], str):
            out[key] = _parse_list_value(out[key])
    out["trigger_conditions"] = _parse_trigger_block(fields)
    return out


class SkillRegistry:
    """Discover and load Markdown skills from skills/<module>/*.md."""

    def __init__(self, skills_root: Path | str | None = None):
        """Initialize SkillRegistry with the provided arguments."""
        self.skills_root = Path(skills_root) if skills_root else default_skills_root()
        self._skills: dict[str, LoadedSkill] = {}
        self._discover()

    def reload(self) -> None:
        """Reload for SkillRegistry."""
        self._skills.clear()
        self._discover()

    def _discover(self) -> None:
        """Discover for SkillRegistry."""
        if not self.skills_root.is_dir():
            _log.warning("skills_root_missing", path=str(self.skills_root))
            return
        for module_dir in sorted(self.skills_root.iterdir()):
            if not module_dir.is_dir():
                continue
            for md_path in sorted(module_dir.glob("*.md")):
                if md_path.name.lower() == "readme.md":
                    continue
                try:
                    skill = self._parse_skill_file(md_path, module_dir.name)
                    self._skills[skill.name] = skill
                except Exception as e:
                    _log.warning("skill_load_failed", path=str(md_path), error=str(e))

    def _parse_skill_file(self, path: Path, module: str) -> LoadedSkill:
        """Parse Skill File for SkillRegistry."""
        text = path.read_text(encoding="utf-8")
        fields, body = _parse_frontmatter(text)
        if not fields.get("name"):
            fields["name"] = path.stem
        if not fields.get("module"):
            fields["module"] = module
        fm = _coerce_frontmatter(fields)
        sections = _parse_sections(body)
        if not sections and body.strip():
            sections["body"] = body.strip()
        agents = fm.get("agents") or []
        if isinstance(agents, str):
            agents = _parse_list_value(agents)
        fix_types = fm.get("fix_types") or []
        if isinstance(fix_types, str):
            fix_types = _parse_list_value(fix_types)
        tool_budget = int(fm.get("tool_budget", "10"))
        recursion_limit = int(fm.get("recursion_limit", str(max(1, tool_budget // 2))))
        return LoadedSkill(
            name=str(fm.get("name", path.stem)),
            module=str(fm.get("module", module)),
            description=str(fm.get("description", "")).strip(),
            source_path=str(path.resolve()),
            agents=list(agents),
            fix_types=list(fix_types),
            trigger_conditions=dict(fm.get("trigger_conditions") or {}),
            sections=sections,
            frontmatter=fm,
            tool_budget=tool_budget,
            recursion_limit=recursion_limit,
        )

    def all_skills(self) -> list[LoadedSkill]:
        """All Skills for SkillRegistry."""
        return list(self._skills.values())

    def get(self, name: str) -> LoadedSkill | None:
        """Get for SkillRegistry."""
        return self._skills.get(name)

    def get_agent_base_skill(self, agent: str) -> LoadedSkill | None:
        """Get Agent Base Skill for SkillRegistry."""
        module = _AGENT_MODULE_MAP.get(agent, agent)
        for skill in self._skills.values():
            if skill.module == module and skill.name == module:
                return skill
        for skill in self._skills.values():
            if skill.module == module and skill.source_path.endswith(f"/{module}.md"):
                return skill
        return None

    def _matches_agent(self, skill: LoadedSkill, agent: str) -> bool:
        """Matches Agent for SkillRegistry."""
        if not skill.agents:
            return True
        mapped = _AGENT_MODULE_MAP.get(agent, agent)
        return agent in skill.agents or mapped in skill.agents or "implement" in skill.agents and mapped == "executor"

    @staticmethod
    def _category_matches_triggers(category: str, trigger_labels: set[str]) -> bool:
        """Category Matches Triggers for SkillRegistry."""
        if not category or not trigger_labels:
            return False
        norm = category.lower().strip()
        tokens = {
            norm,
            norm.replace(" ", "-"),
            norm.replace("-", " "),
            norm.replace("_", "-"),
        }
        tokens.update(part for part in norm.replace("-", " ").split() if part)
        return bool(tokens & trigger_labels)

    def _matches_ticket(self, skill: LoadedSkill, ticket: JiraTicket | None) -> bool:
        """Matches Ticket for SkillRegistry."""
        triggers = skill.trigger_conditions
        if not triggers:
            return skill.module not in ("security",)
        if ticket is None:
            return False
        labels = {lbl.lower() for lbl in ticket.get("labels", [])}
        trigger_labels = {lbl.lower() for lbl in triggers.get("labels", [])}
        labels_matched = bool(trigger_labels and (labels & trigger_labels))
        category = (ticket.get("vulnerability_category") or "").strip()
        category_matched = self._category_matches_triggers(category, trigger_labels)
        if trigger_labels and not (labels_matched or category_matched):
            return False
        issue_types = {t.lower() for t in triggers.get("issue_types", [])}
        if issue_types and (ticket.get("issue_type") or "").lower() not in issue_types:
            return False
        langs = {lang.lower() for lang in triggers.get("languages", [])}
        if langs and not labels_matched:
            summary = (
                (ticket.get("problem_statement") or ticket.get("title") or "")
                + " "
                + " ".join(ticket.get("labels", []))
            ).lower()
            if not any(lang in summary for lang in langs):
                return False
        return True

    def _matches_fix_plan(self, skill: LoadedSkill, fix_plan: FixPlan | None) -> bool:
        """Matches Fix Plan for SkillRegistry."""
        if not skill.fix_types:
            return True
        if fix_plan is None:
            return False
        primary = fix_plan.primary_fix_type
        target_types = {t.fix_type for t in fix_plan.targets}
        allowed = set(skill.fix_types)
        return primary in allowed or bool(target_types & allowed)

    def select(
        self,
        agent: str,
        *,
        ticket: JiraTicket | None = None,
        fix_plan: FixPlan | None = None,
    ) -> list[LoadedSkill]:
        """Select for SkillRegistry."""
        selected: list[LoadedSkill] = []
        seen: set[str] = set()

        base = self.get_agent_base_skill(agent)
        if base and base.name not in seen:
            selected.append(base)
            seen.add(base.name)

        for skill in self._skills.values():
            if skill.name in seen:
                continue
            if skill.module == _AGENT_MODULE_MAP.get(agent, agent) and skill.name == skill.module:
                continue
            if not self._matches_agent(skill, agent):
                continue
            if not self._matches_ticket(skill, ticket):
                continue
            if skill.fix_types and not self._matches_fix_plan(skill, fix_plan):
                continue
            if skill.module == "security" or skill.trigger_conditions:
                selected.append(skill)
                seen.add(skill.name)
                _log.info("skill_selected", agent=agent, skill=skill.name, path=skill.source_path)

        return selected

    @staticmethod
    def format_for_prompt(skills: list[LoadedSkill]) -> str:
        """Format For Prompt for SkillRegistry."""
        if not skills:
            return ""
        parts: list[str] = ["## Loaded Skills\n"]
        for skill in skills:
            parts.append(f"### Skill: {skill.name} ({skill.module})\n")
            if skill.description:
                parts.append(f"{skill.description}\n")
            for section in (
                "prompt_templates",
                "implementation_guidance",
                "description",
                "body",
            ):
                content = skill.section(section)
                if content:
                    parts.append(f"\n#### {section}\n{content}\n")
        return "".join(parts)


def format_domain_skills_for_prompt(agent: str, ticket: JiraTicket | None) -> str:
    """Return formatted security domain skills for injection into agent prompts."""
    if not ticket:
        return ""
    selected = get_skill_registry().select(agent, ticket=ticket)
    domain = [s for s in selected if s.module == "security"]
    if not domain:
        return ""
    return SkillRegistry.format_for_prompt(domain)


_default_registry: SkillRegistry | None = None


def get_skill_registry(skills_root: Path | str | None = None) -> SkillRegistry:
    """Return skill registry."""
    global _default_registry
    if skills_root is not None:
        return SkillRegistry(skills_root)
    if _default_registry is None:
        _default_registry = SkillRegistry()
    return _default_registry

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
        """Build for PromptBuilder."""
        manifest = load_skill_manifest(skills_dir)
        parts: list[str] = [load_skill_body(skills_dir)]

        if include_references:
            parts.append(format_reference_sections(skills_dir, select_references(ticket, skills_dir, agent)))

        if extra_instructions:
            parts.append(f"\n\n{extra_instructions}")

        parts.append(f"\n\n## CONTEXT\n{json.dumps(context, indent=2, default=str)}\n")
        return "".join(parts), manifest

    @staticmethod
    def build_system_only(skills_dir: Path) -> tuple[str, SkillManifest]:
        """Level-2 body only — for research system prompt without duplicating context."""
        manifest = load_skill_manifest(skills_dir)
        return load_skill_body(skills_dir), manifest

class SkillManager:
    """Single entry point for skill discovery, selection, and prompt assembly."""

    @staticmethod
    def build_prompt(
        agent: str,
        skills_dir: Path,
        context: dict[str, Any],
        *,
        ticket: JiraTicket | None = None,
        include_references: bool = True,
        extra_instructions: str = "",
    ) -> tuple[str, SkillManifest]:
        """Build Prompt for SkillManager."""
        return PromptBuilder.build(
            agent,
            skills_dir,
            context,
            ticket=ticket,
            include_references=include_references,
            extra_instructions=extra_instructions,
        )

    @staticmethod
    def build_system_only(skills_dir: Path) -> tuple[str, SkillManifest]:
        """Build System Only for SkillManager."""
        return PromptBuilder.build_system_only(skills_dir)

    @staticmethod
    def registry(skills_root: Path | str | None = None) -> SkillRegistry:
        """Registry for SkillManager."""
        return get_skill_registry(skills_root)
