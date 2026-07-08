"""PipelineArtifact — frozen snapshot of one integration eval run per case."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

RUNS_ROOT = Path("evals/runs")
CONTEXT_PADDING = 50


class FileContext(BaseModel):
    """±CONTEXT_PADDING lines around an edit target."""

    file: str
    function: str | None = None
    code: str = ""


class PipelineArtifact(BaseModel):
    """Full pipeline output for one golden case — input to preflight + LLM judges."""

    case_id: str
    run_id: str
    captured_at: str
    artifact_hash: str = ""

    ticket: dict[str, Any] = Field(default_factory=dict)
    repo: dict[str, Any] = Field(default_factory=dict)
    golden: dict[str, Any] = Field(default_factory=dict)

    route: str | None = None
    research_output: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    plan_validation: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] | None = None
    executor_validations: list[dict[str, Any]] = Field(default_factory=list)
    git_diff: str = ""
    changed_files: list[str] = Field(default_factory=list)
    step_results: list[dict[str, Any]] = Field(default_factory=list)

    before_context: list[FileContext] = Field(default_factory=list)
    after_context: list[FileContext] = Field(default_factory=list)

    error: str | None = None
    stage_failed: str | None = None
    token_usage: dict[str, Any] = Field(default_factory=dict)
    cursor_request_count: int = 0
    dry_run: bool = False

    def compute_hash(self) -> str:
        """Hash judge-relevant payload for cache keys."""
        payload = {
            "case_id": self.case_id,
            "commit": self.repo.get("commit_sha", ""),
            "plan": self.plan,
            "git_diff": self.git_diff,
            "before": [c.model_dump() for c in self.before_context],
            "after": [c.model_dump() for c in self.after_context],
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def finalize(self) -> "PipelineArtifact":
        """Stamp artifact_hash after all fields populated."""
        self.artifact_hash = self.compute_hash()
        return self


class RunMetadata(BaseModel):
    """Metadata for one immutable eval run directory."""

    run_id: str
    created_at: str
    mode: str = "integration"
    cases_path: str = ""
    dry_run: bool = False
    judge_tier: str = ""
    judge_model: str = ""
    judge_version: str = "v1"
    experiment: str = ""


def allocate_run_dir(runs_root: Path | str = RUNS_ROOT) -> tuple[str, Path]:
    """Return (run_id, path) for a new immutable run directory."""
    root = Path(runs_root)
    root.mkdir(parents=True, exist_ok=True)
    existing = []
    for p in root.iterdir():
        if p.is_dir() and p.name.startswith("run_"):
            m = re.match(r"run_(\d+)$", p.name)
            if m:
                existing.append(int(m.group(1)))
    n = max(existing, default=0) + 1
    run_id = f"run_{n:03d}"
    run_path = root / run_id
    run_path.mkdir(parents=True, exist_ok=True)
    (run_path / "artifacts").mkdir(exist_ok=True)
    (run_path / "judge_results" / "planner").mkdir(parents=True, exist_ok=True)
    (run_path / "judge_results" / "executor").mkdir(parents=True, exist_ok=True)
    return run_id, run_path


def run_dir(run_id: str, runs_root: Path | str = RUNS_ROOT) -> Path:
    """Path to an existing run directory."""
    return Path(runs_root) / run_id


def artifact_path(run_id: str, case_id: str, runs_root: Path | str = RUNS_ROOT) -> Path:
    """Path to one case artifact JSON."""
    return run_dir(run_id, runs_root) / "artifacts" / f"{case_id}.json"


def save_artifact(artifact: PipelineArtifact, runs_root: Path | str = RUNS_ROOT) -> Path:
    """Write artifact to evals/runs/{run_id}/artifacts/{case_id}.json."""
    artifact.finalize()
    path = artifact_path(artifact.run_id, artifact.case_id, runs_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_artifact(run_id: str, case_id: str, runs_root: Path | str = RUNS_ROOT) -> PipelineArtifact:
    """Load one case artifact."""
    path = artifact_path(run_id, case_id, runs_root)
    return PipelineArtifact.model_validate_json(path.read_text(encoding="utf-8"))


def load_run_artifacts(run_id: str, runs_root: Path | str = RUNS_ROOT) -> dict[str, PipelineArtifact]:
    """Load all artifacts for a run."""
    art_dir = run_dir(run_id, runs_root) / "artifacts"
    out: dict[str, PipelineArtifact] = {}
    if not art_dir.is_dir():
        return out
    for f in sorted(art_dir.glob("*.json")):
        art = PipelineArtifact.model_validate_json(f.read_text(encoding="utf-8"))
        out[art.case_id] = art
    return out


def save_run_metadata(meta: RunMetadata, runs_root: Path | str = RUNS_ROOT) -> Path:
    """Write run metadata.json."""
    path = run_dir(meta.run_id, runs_root) / "metadata.json"
    path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_run_metadata(run_id: str, runs_root: Path | str = RUNS_ROOT) -> RunMetadata | None:
    """Load run metadata if present."""
    path = run_dir(run_id, runs_root) / "metadata.json"
    if not path.is_file():
        return None
    return RunMetadata.model_validate_json(path.read_text(encoding="utf-8"))


def artifact_hash(*parts: Any) -> str:
    """Generic hash helper for judge cache keys."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def read_lines_at(
    repo_path: str,
    file_path: str,
    center_line: int,
    *,
    padding: int = CONTEXT_PADDING,
) -> str:
    """Read ±padding lines around center_line (1-indexed)."""
    full = Path(repo_path) / file_path
    if not full.is_file():
        return ""
    try:
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    start = max(1, center_line - padding)
    end = min(len(lines), center_line + padding)
    snippet_lines = []
    for i in range(start, end + 1):
        snippet_lines.append(f"{i:5d}| {lines[i - 1]}")
    return "\n".join(snippet_lines)


def capture_file_context(
    repo_path: str,
    file_path: str,
    *,
    center_line: int = 1,
    function: str | None = None,
    padding: int = CONTEXT_PADDING,
) -> FileContext:
    """Build FileContext from on-disk file."""
    return FileContext(
        file=file_path,
        function=function,
        code=read_lines_at(repo_path, file_path, center_line, padding=padding),
    )


def build_golden_snapshot(case: Any) -> dict[str, Any]:
    """Extract golden answer-key fields from EvalCase."""
    return {
        "pass_criteria": list(case.pass_criteria or []),
        "expected_files": list(case.expected_files or []),
        "expected_fix_type": case.expected_fix_type or "",
        "expected_patch_contains": list(case.expected_patch_contains or []),
        "expected_patch_excludes": list(case.expected_patch_excludes or []),
        "vuln_class": case.vuln_class or "",
        "cwe": case.cwe or "",
        "owasp_category": case.owasp_category or "",
        "difficulty": case.difficulty or "",
    }


def now_iso() -> str:
    """UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()
