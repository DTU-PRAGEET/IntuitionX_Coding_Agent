"""Runtime Skills Plugin System — discover, parse, and select Markdown skills."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.logging import get_logger
from src.core.schemas import FixPlan
from src.core.skills_loader import _parse_frontmatter
from src.core.state import JiraTicket

_log = get_logger("skill_registry")

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
    root = skills_root or default_skills_root()
    return root / module


@dataclass
class LoadedSkill:
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
        return self.sections.get(name, "")


def _parse_list_value(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_trigger_block(fm: dict[str, str]) -> dict[str, Any]:
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
    out: dict[str, Any] = dict(fields)
    for key in ("agents", "fix_types"):
        if key in out and isinstance(out[key], str):
            out[key] = _parse_list_value(out[key])
    out["trigger_conditions"] = _parse_trigger_block(fields)
    return out


class SkillRegistry:
    """Discover and load Markdown skills from skills/<module>/*.md."""

    def __init__(self, skills_root: Path | str | None = None):
        self.skills_root = Path(skills_root) if skills_root else default_skills_root()
        self._skills: dict[str, LoadedSkill] = {}
        self._discover()

    def reload(self) -> None:
        self._skills.clear()
        self._discover()

    def _discover(self) -> None:
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
        return list(self._skills.values())

    def get(self, name: str) -> LoadedSkill | None:
        return self._skills.get(name)

    def get_agent_base_skill(self, agent: str) -> LoadedSkill | None:
        module = _AGENT_MODULE_MAP.get(agent, agent)
        for skill in self._skills.values():
            if skill.module == module and skill.name == module:
                return skill
        for skill in self._skills.values():
            if skill.module == module and skill.source_path.endswith(f"/{module}.md"):
                return skill
        return None

    def _matches_agent(self, skill: LoadedSkill, agent: str) -> bool:
        if not skill.agents:
            return True
        mapped = _AGENT_MODULE_MAP.get(agent, agent)
        return agent in skill.agents or mapped in skill.agents or "implement" in skill.agents and mapped == "executor"

    def _matches_ticket(self, skill: LoadedSkill, ticket: JiraTicket | None) -> bool:
        triggers = skill.trigger_conditions
        if not triggers:
            return skill.module not in ("security",)
        if ticket is None:
            return False
        labels = {lbl.lower() for lbl in ticket.get("labels", [])}
        trigger_labels = {lbl.lower() for lbl in triggers.get("labels", [])}
        labels_matched = bool(trigger_labels and (labels & trigger_labels))
        if trigger_labels and not labels_matched:
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


_default_registry: SkillRegistry | None = None


def get_skill_registry(skills_root: Path | str | None = None) -> SkillRegistry:
    global _default_registry
    if skills_root is not None:
        return SkillRegistry(skills_root)
    if _default_registry is None:
        _default_registry = SkillRegistry()
    return _default_registry
