"""Canonical eval-case schema (Milestone 2.1).

A golden case is the SOURCE OF TRUTH for one benchmark scenario. It carries:
  * the INPUT the pipeline receives  -> `ticket` (becomes a JiraTicket)
  * the EXPECTED OUTPUT (answer key) -> expected_route / expected_files / ...

Cases are immutable once baselined (Milestone 2.2). Never edit a baselined
case; only add new ones.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator

Difficulty = str  # "easy" | "medium" | "hard"
Route = str       # "FULL" | "SKIP_RESEARCH" | "MICRO" | "EXIT"
EvalScope = str   # "routing" | "research" | "plan" | "implement"
CaseKind = str    # "unit" | "integration"


class RepoSnapshot(BaseModel):
    """Pinned, reproducible repo reference. EXIT cases may omit the repo."""

    slug: str = ""                       # e.g. "Testing-Security-demo"
    url: str = ""                        # full GitHub fork URL
    commit_sha: str = ""                 # MUST be pinned before a baseline run
    target_branch: str = "main"
    local_fixture_path: str = ""         # optional: snapshot dir checked into evals/fixtures/

    def is_pinned(self) -> bool:
        """Is Pinned for RepoSnapshot."""
        return bool(self.commit_sha)


class TicketInput(BaseModel):
    """The bug ticket the pipeline ingests (the eval INPUT).

    Maps 1:1 onto src.core.state.JiraTicket via harness.ticket_builder. Framing
    here (labels, named files, status) drives the orchestrator routing decision,
    so it must be authored intentionally per the expected_route.
    """

    title: str
    problem_statement: str
    issue_type: str = "Security"
    status: str = "In Progress"
    priority_raw: str = "High"
    priority_normalized: str = "P1"
    module: str = ""
    labels: list[str] = Field(default_factory=list)
    repo_links: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    vulnerability_category: str = ""


class ExpectedLineRange(BaseModel):
    """Answer-key line span for one file in a golden eval case.
    Compared against Prediction.plan_line_ranges by `evaluators/scoring.plan_line_overlap`."""
    file: str
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class IssueExpectation(BaseModel):
    """One security issue inside an integration case (may span a specific repo)."""

    issue_id: str                                  # "I-01-a"
    title: str
    repo_slug: str = ""
    vuln_class: str = ""
    cwe: str = ""
    expected_files: list[str] = Field(default_factory=list)
    expected_line_ranges: list[ExpectedLineRange] = Field(default_factory=list)
    expected_fix_type: str = ""
    expected_patch_contains: list[str] = Field(default_factory=list)
    expected_patch_excludes: list[str] = Field(default_factory=list)
    source_unit_case: str = ""                     # provenance, e.g. "G-01"


class EvalCase(BaseModel):
    """Golden benchmark scenario — ticket input + expected route/files/patch (Milestone 2.1).
    Loaded from `evals/golden/`; drives `runners/run_eval.py` and LangSmith sync."""
    case_id: str                                   # "G-01" (unit) | "I-01" (integration)
    title: str
    case_kind: CaseKind = "unit"                   # unit = per-agent; integration = multi-repo e2e
    repo: RepoSnapshot = Field(default_factory=RepoSnapshot)
    repos: list[RepoSnapshot] = Field(default_factory=list)  # integration: all pinned repos
    language: str = ""                             # python | java | javascript | php | ruby | ...

    # Taxonomy (Milestone 2.2 coverage matrix + OWASP)
    vuln_class: str = ""                           # Injection | XSS | Auth | BAC | IDOR | Crypto | Secrets | Misconfig | Routing | ...
    owasp_category: str = ""                       # "A05:2025"
    cwe: str = ""                                  # "CWE-89"
    severity_label: str = ""                       # Critical | High | Medium | Low
    difficulty: Difficulty = "medium"

    # INPUT
    ticket: TicketInput

    # EXPECTED OUTPUT (answer key — sourced from docs/Testing_repos_info/*)
    expected_route: Route
    expected_files: list[str] = Field(default_factory=list)
    expected_line_ranges: list[ExpectedLineRange] = Field(default_factory=list)
    expected_fix_type: str = ""                    # maps to schemas.FixType where possible
    expected_patch_contains: list[str] = Field(default_factory=list)
    expected_patch_excludes: list[str] = Field(default_factory=list)
    pass_criteria: list[str] = Field(default_factory=list)
    issues: list[IssueExpectation] = Field(default_factory=list)  # integration: per-issue answer key

    # Harness control
    eval_scope: list[EvalScope] = Field(default_factory=lambda: ["routing", "research", "plan", "implement"])
    requires_cursor: bool = True

    @field_validator("case_kind")
    @classmethod
    def _valid_case_kind(cls, v: str) -> str:
        """Valid Case Kind for EvalCase."""
        allowed = {"unit", "integration"}
        if v not in allowed:
            raise ValueError(f"case_kind must be one of {sorted(allowed)}, got {v!r}")
        return v

    @field_validator("expected_route")
    @classmethod
    def _valid_route(cls, v: str) -> str:
        """Valid Route for EvalCase."""
        allowed = {"FULL", "SKIP_RESEARCH", "MICRO", "EXIT"}
        if v not in allowed:
            raise ValueError(f"expected_route must be one of {sorted(allowed)}, got {v!r}")
        return v

    @field_validator("difficulty")
    @classmethod
    def _valid_difficulty(cls, v: str) -> str:
        """Valid Difficulty for EvalCase."""
        allowed = {"easy", "medium", "hard"}
        if v not in allowed:
            raise ValueError(f"difficulty must be one of {sorted(allowed)}, got {v!r}")
        return v

    def all_repos(self) -> list[RepoSnapshot]:
        """Repos involved in this case (integration may list several)."""
        if self.repos:
            return list(self.repos)
        if self.repo.url or self.repo.local_fixture_path:
            return [self.repo]
        return []

    def warnings(self) -> list[str]:
        """Non-fatal data-quality issues surfaced at load/validate time."""
        out: list[str] = []
        if self.expected_route != "EXIT":
            repos = self.all_repos()
            if repos and not all(r.is_pinned() for r in repos):
                out.append(f"{self.case_id}: one or more repos lack a pinned commit_sha")
            elif not repos and self.case_kind != "integration":
                out.append(f"{self.case_id}: repo.commit_sha is not pinned (results will not be reproducible)")
            if self.case_kind == "integration":
                if len(self.issues) < 2:
                    out.append(f"{self.case_id}: integration case should list at least 2 issues")
            if "research" in self.eval_scope and not self.expected_files:
                out.append(f"{self.case_id}: research in scope but expected_files is empty")
            if "plan" in self.eval_scope and not self.expected_line_ranges:
                out.append(f"{self.case_id}: plan in scope but expected_line_ranges is empty")
        return out


def _cases_from_json(data: object) -> list[EvalCase]:
    """Cases From Json for this module."""
    if isinstance(data, list):
        return [EvalCase.model_validate(item) for item in data]
    return [EvalCase.model_validate(data)]


def load_case(path: str | Path) -> EvalCase:
    """Load case."""
    cases = _cases_from_json(json.loads(Path(path).read_text(encoding="utf-8")))
    if len(cases) != 1:
        raise ValueError(f"load_case expects exactly one case in {path}, found {len(cases)}")
    return cases[0]


def _iter_case_files(p: Path) -> list[Path]:
    """Iter Case Files for this module."""
    if p.is_file():
        return [p]
    return sorted(p.rglob("*.json"))


def load_cases(target: str | Path) -> list[EvalCase]:
    """Load every *.json under a directory (recursive), or a single file path."""
    p = Path(target)
    if not p.exists():
        raise FileNotFoundError(f"cases path does not exist: {p}")
    cases: list[EvalCase] = []
    for f in _iter_case_files(p):
        cases.extend(_cases_from_json(json.loads(f.read_text(encoding="utf-8"))))
    return sorted(cases, key=lambda c: c.case_id)


def load_unit_cases(target: str | Path = "evals/golden/unit_test") -> list[EvalCase]:
    """Load unit cases."""
    return [c for c in load_cases(target) if c.case_kind == "unit"]


def load_integration_cases(target: str | Path = "evals/golden/integration_test") -> list[EvalCase]:
    """Load integration cases."""
    return [c for c in load_cases(target) if c.case_kind == "integration"]


def validate_dir(target: str | Path) -> tuple[int, list[str]]:
    """Validate all cases; return (count, warnings). Raises on schema errors."""
    cases = load_cases(target)
    seen: set[str] = set()
    warnings: list[str] = []
    for c in cases:
        if c.case_id in seen:
            raise ValueError(f"duplicate case_id: {c.case_id}")
        seen.add(c.case_id)
        warnings.extend(c.warnings())
    return len(cases), warnings


if __name__ == "__main__":  # `python -m evals.schema.case <dir>`
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "evals/golden/unit_test"
    count, warns = validate_dir(target)
    print(f"OK: {count} case(s) passed schema validation in {target}")
    for w in warns:
        print(f"  WARN  {w}")
