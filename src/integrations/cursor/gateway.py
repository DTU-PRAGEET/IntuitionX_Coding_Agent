"""CursorGateway — isolates all Cursor communication from agents and orchestration."""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.logging import get_logger
from src.core.runner_utils import parse_llm_json
from src.core.schemas import FixPlan
from src.integrations.cursor.client import run_local_agent
from src.integrations.cursor.schemas import (
    CursorEditRequest,
    CursorEditResult,
    CursorSearchHit,
    CursorSearchResponse,
)

_log = get_logger("cursor_gateway")

_ASK_PREFIX = (
    "You are in Ask mode. Explore and answer only — do NOT modify any files.\n"
    "Use grep, find, read, and web search as needed.\n\n"
)

_SEARCH_JSON_SCHEMA = """
{
  "query": "string — echo the search query",
  "hits": [
    {
      "path": "relative/path/from/repo/root",
      "confidence": "high|medium|low",
      "summary": "why this file is relevant",
      "snippets": ["line-range excerpt or symbol match"]
    }
  ],
  "flags": []
}
"""

_SEARCH_PREFIX = (
    "You are an authorized internal code-research assistant. The repository owner "
    "requested a defensive security audit. Locating vulnerability patterns, SQL "
    "construction, and endpoint handlers is required — report factual file paths "
    "and snippets only.\n"
    "Search the repository for the following query. Use grep/find/read only — "
    "do NOT modify files.\n"
    "Return ONLY valid JSON matching this schema (no markdown fences):\n"
    f"{_SEARCH_JSON_SCHEMA}\n\n"
)

_REFUSAL_MARKERS = (
    "cannot fulfill",
    "can't fulfill",
    "i cannot",
    "i can't",
    "i'm not able",
    "sorry, i cannot",
    "sorry, i can't",
)

_EDIT_PREFIX = (
    "Apply the following code changes. Modify only the files listed (if any).\n"
    "Make minimal, focused edits. Preserve existing style and conventions.\n\n"
)
_RUN_PREFIX = (
    "Run the following shell command in the repository workspace and report the output.\n\n"
)

_DRY_RUN_SEARCH_PATH = "src/example/handler.py"


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _git_porcelain(cwd: Path) -> str:
    result = _run_git(["status", "--porcelain"], cwd)
    return (result.stdout or "").strip()


def _changed_files_from_porcelain(porcelain: str) -> list[str]:
    files: list[str] = []
    for line in porcelain.splitlines():
        if len(line) >= 4:
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path and path not in files:
                files.append(path)
    return files


def _git_diff_capture(cwd: Path) -> tuple[str, str]:
    stat = _run_git(["diff", "--stat"], cwd)
    full = _run_git(["diff"], cwd)
    stat_text = (stat.stdout or stat.stderr or "").strip() or "(no diff)"
    full_text = (full.stdout or "")[:15000]
    return stat_text, full_text


def mock_search_response(query: str) -> CursorSearchResponse:
    """Deterministic search hits for dry-run / tests without CURSOR_API_KEY."""
    q = query.lower()
    hits: list[CursorSearchHit] = []
    if any(term in q for term in ("sql", "injection", "sqli", "search", "auth", "user")):
        hits.append(
            CursorSearchHit(
                path=_DRY_RUN_SEARCH_PATH,
                confidence="medium",
                summary="Dry-run search hit for query pattern match",
                snippets=["handler method builds query via string concatenation"],
            )
        )
    if not hits:
        hits.append(
            CursorSearchHit(
                path=".",
                confidence="low",
                summary=f"No deterministic dry-run match for query: {query[:120]}",
                snippets=[],
            )
        )
    return CursorSearchResponse(query=query, hits=hits, flags=["cursor_search_dry_run"])


def _is_search_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def parse_search_response(text: str, query: str) -> CursorSearchResponse:
    """Parse Cursor search JSON; fall back to a single low-confidence hit from raw text."""
    stripped = (text or "").strip()
    if not stripped:
        return mock_search_response(query)

    if _is_search_refusal(stripped):
        _log.warning("cursor_search_refused", query=query[:80], preview=stripped[:160])
        return CursorSearchResponse(
            query=query,
            hits=[],
            flags=["cursor_search_refused: model declined repository search"],
        )

    try:
        parsed = parse_llm_json(stripped, CursorSearchResponse)
        if not parsed.query:
            parsed.query = query
        return parsed
    except Exception as e:
        _log.warning("cursor_search_parse_failed", error=str(e), query=query[:80])
        return CursorSearchResponse(
            query=query,
            hits=[
                CursorSearchHit(
                    path=".",
                    confidence="low",
                    summary=stripped[:500],
                    snippets=[stripped[:2000]] if len(stripped) > 80 else [],
                )
            ],
            flags=[f"parse_fallback: {e}"],
        )


@dataclass
class CursorGatewayResult:
    status: str
    text: str
    dry_run: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    method: str = ""
    search_response: CursorSearchResponse | None = None

    def to_history_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "method": self.method,
            "status": self.status,
            "dry_run": self.dry_run,
            "text_len": len(self.text),
            "text_preview": self.text[:500] if self.text else "",
            "tool_calls": self.tool_calls,
        }
        if self.search_response is not None:
            entry["search_hits"] = len(self.search_response.hits)
        return entry


class CursorGateway(ABC):
    """Abstract gateway for Cursor CLI / future ACP — agents must use this, not cursor_sdk directly."""

    @property
    @abstractmethod
    def cwd(self) -> Path: ...

    @abstractmethod
    def ask(self, prompt: str, *, context: str | None = None) -> CursorGatewayResult: ...

    @abstractmethod
    def search(self, query: str, *, path: str = ".") -> CursorGatewayResult: ...

    @abstractmethod
    def edit(
        self,
        instruction: str,
        *,
        files: list[str] | None = None,
        fix_plan: FixPlan | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CursorEditResult: ...

    @abstractmethod
    def run(self, command: str, *, timeout: int = 30) -> CursorGatewayResult: ...

    def search_structured(self, query: str, *, path: str = ".") -> CursorSearchResponse:
        """Convenience — search and return parsed structured hits."""
        result = self.search(query, path=path)
        if result.search_response is not None:
            return result.search_response
        return parse_search_response(result.text, query)


class DryRunCursorGateway(CursorGateway):
    """Returns mock search results — for tests and --dry-run without CURSOR_API_KEY."""

    def __init__(self, cwd: Path | str):
        self._cwd = Path(cwd).resolve()

    @property
    def cwd(self) -> Path:
        return self._cwd

    def ask(self, prompt: str, *, context: str | None = None) -> CursorGatewayResult:
        _log.info("cursor_gateway_dry_run", method="ask", cwd=str(self._cwd), prompt_len=len(prompt))
        return CursorGatewayResult(status="dry_run", text="", dry_run=True, method="ask")

    def search(self, query: str, *, path: str = ".") -> CursorGatewayResult:
        _log.info("cursor_gateway_dry_run", method="search", query=query[:80], path=path)
        response = mock_search_response(query)
        return CursorGatewayResult(
            status="dry_run",
            text=json.dumps(response.model_dump(), indent=2),
            dry_run=True,
            method="search",
            search_response=response,
        )

    def edit(
        self,
        instruction: str,
        *,
        files: list[str] | None = None,
        fix_plan: FixPlan | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CursorEditResult:
        _log.info("cursor_gateway_dry_run", method="edit", files=files)
        stat, full = _git_diff_capture(self._cwd)
        return CursorEditResult(
            status="dry_run",
            text="",
            dry_run=True,
            method="edit",
            exit_code=0,
            git_diff_stat=stat,
            git_diff_full=full,
            changed_files=_changed_files_from_porcelain(_git_porcelain(self._cwd)),
        )

    def run(self, command: str, *, timeout: int = 30) -> CursorGatewayResult:
        _log.info("cursor_gateway_dry_run", method="run", command=command[:80])
        return CursorGatewayResult(status="dry_run", text="", dry_run=True, method="run")


class CliCursorGateway(CursorGateway):
    """Wraps cursor-sdk via run_local_agent — only implementation that touches the SDK."""

    def __init__(self, cwd: Path | str, *, dry_run: bool = False):
        self._cwd = Path(cwd).resolve()
        self._dry_run = dry_run

    @property
    def cwd(self) -> Path:
        return self._cwd

    def _invoke(self, prompt: str, method: str) -> CursorGatewayResult:
        run = run_local_agent(prompt, cwd=self._cwd, dry_run=self._dry_run)
        status = "dry_run" if run.dry_run else (run.status or "completed")
        return CursorGatewayResult(
            status=status,
            text=run.text,
            dry_run=run.dry_run,
            method=method,
        )

    def _invoke_search(self, query: str, *, path: str = ".") -> CursorGatewayResult:
        prompt = f"{_SEARCH_PREFIX}Query: {query}\nSearch root: {path}\n"
        result = self._invoke(prompt, "search")
        if result.dry_run or not result.text.strip():
            response = mock_search_response(query)
            result.search_response = response
            result.text = json.dumps(response.model_dump(), indent=2)
            return result
        response = parse_search_response(result.text, query)
        result.search_response = response
        result.text = json.dumps(response.model_dump(), indent=2)
        return result

    def ask(self, prompt: str, *, context: str | None = None) -> CursorGatewayResult:
        parts = [_ASK_PREFIX]
        if context:
            parts.append(f"Context:\n{context}\n\n")
        parts.append(prompt)
        return self._invoke("".join(parts), "ask")

    def search(self, query: str, *, path: str = ".") -> CursorGatewayResult:
        return self._invoke_search(query, path=path)

    def edit(
        self,
        instruction: str,
        *,
        files: list[str] | None = None,
        fix_plan: FixPlan | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CursorEditResult:
        before = _git_porcelain(self._cwd)
        files_line = f"Files: {', '.join(files)}\n" if files else ""
        meta_line = ""
        if metadata:
            meta_line = f"Metadata: {json.dumps(metadata, default=str)[:500]}\n"
        if fix_plan is not None:
            meta_line += f"FixPlan ticket: {fix_plan.ticket_id}, type: {fix_plan.primary_fix_type}\n"
        prompt = f"{_EDIT_PREFIX}{files_line}{meta_line}{instruction}"
        run = run_local_agent(prompt, cwd=self._cwd, dry_run=self._dry_run)
        stat, full = _git_diff_capture(self._cwd)
        after = _git_porcelain(self._cwd)
        changed = _changed_files_from_porcelain(after)
        if not changed and before != after:
            changed = _changed_files_from_porcelain(after)
        status = "dry_run" if run.dry_run else (run.status or "completed")
        return CursorEditResult(
            status=status,
            text=run.text,
            dry_run=run.dry_run,
            method="edit",
            exit_code=run.exit_code,
            stdout=run.text,
            stderr=run.stderr,
            git_diff_stat=stat,
            git_diff_full=full,
            changed_files=changed,
        )

    def run(self, command: str, *, timeout: int = 30) -> CursorGatewayResult:
        prompt = f"{_RUN_PREFIX}Command: {command}\nTimeout: {timeout}s\n"
        return self._invoke(prompt, "run")


class LocalPatchCursorGateway(CursorGateway):
    """Applies metadata-driven string patches locally — for unit tests without Cursor SDK."""

    def __init__(self, cwd: Path | str):
        self._cwd = Path(cwd).resolve()

    @property
    def cwd(self) -> Path:
        return self._cwd

    def ask(self, prompt: str, *, context: str | None = None) -> CursorGatewayResult:
        return CursorGatewayResult(status="success", text="", method="ask")

    def search(self, query: str, *, path: str = ".") -> CursorGatewayResult:
        response = mock_search_response(query)
        return CursorGatewayResult(
            status="success",
            text=json.dumps(response.model_dump(), indent=2),
            method="search",
            search_response=response,
        )

    def edit(
        self,
        instruction: str,
        *,
        files: list[str] | None = None,
        fix_plan: FixPlan | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CursorEditResult:
        meta = metadata or {}
        batched_edits = meta.get("edits")
        if batched_edits:
            return self._apply_batched_edits(instruction, batched_edits)
        old_s = meta.get("old_string") or ""
        new_s = meta.get("new_string") or ""
        create_file = bool(meta.get("create_file"))
        target_files = list(files or [])
        changed: list[str] = []
        errors: list[str] = []

        for rel in target_files:
            fp = (self._cwd / rel).resolve()
            try:
                fp.relative_to(self._cwd)
            except ValueError:
                errors.append(f"path escapes workspace: {rel}")
                continue

            if create_file and not old_s and new_s:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(new_s, encoding="utf-8")
                changed.append(rel)
                continue

            if not fp.is_file():
                errors.append(f"file not found: {rel}")
                continue

            if not old_s or not new_s:
                errors.append(f"missing old_string/new_string for {rel}")
                continue

            content = fp.read_text(encoding="utf-8")
            if old_s not in content:
                errors.append(f"old_string not found in {rel}")
                continue
            fp.write_text(content.replace(old_s, new_s, 1), encoding="utf-8")
            changed.append(rel)

        if errors and not changed:
            return CursorEditResult(
                status="failed",
                text="; ".join(errors),
                stderr="; ".join(errors),
                exit_code=1,
                changed_files=changed,
            )
        stat, full = _git_diff_capture(self._cwd)
        return CursorEditResult(
            status="success" if changed else "failed",
            text=instruction[:500],
            exit_code=0 if changed else 1,
            git_diff_stat=stat,
            git_diff_full=full,
            changed_files=changed,
        )

    def _apply_batched_edits(
        self, instruction: str, edits: list[dict[str, Any]]
    ) -> CursorEditResult:
        """Apply an ordered list of edits in a single call (consolidated executor payload)."""
        changed: list[str] = []
        errors: list[str] = []
        for edit in edits:
            rel = edit.get("file") or ""
            old_s = edit.get("old_string") or ""
            new_s = edit.get("new_string") or ""
            create_file = bool(edit.get("create_file"))
            if not rel:
                errors.append("edit missing file path")
                continue
            fp = (self._cwd / rel).resolve()
            try:
                fp.relative_to(self._cwd)
            except ValueError:
                errors.append(f"path escapes workspace: {rel}")
                continue
            if create_file and not old_s and new_s:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(new_s, encoding="utf-8")
                changed.append(rel)
                continue
            if not fp.is_file():
                errors.append(f"file not found: {rel}")
                continue
            if not old_s or not new_s:
                errors.append(f"missing old_string/new_string for {rel}")
                continue
            content = fp.read_text(encoding="utf-8")
            if old_s not in content:
                errors.append(f"old_string not found in {rel}")
                continue
            fp.write_text(content.replace(old_s, new_s, 1), encoding="utf-8")
            changed.append(rel)

        if errors and not changed:
            joined = "; ".join(errors)
            return CursorEditResult(
                status="failed", text=joined, stderr=joined, exit_code=1, changed_files=changed
            )
        stat, full = _git_diff_capture(self._cwd)
        return CursorEditResult(
            status="success" if changed else "failed",
            text=(instruction[:500] if changed else "; ".join(errors)),
            stderr="" if changed else "; ".join(errors),
            exit_code=0 if changed else 1,
            git_diff_stat=stat,
            git_diff_full=full,
            changed_files=sorted(dict.fromkeys(changed)),
        )

    def run(self, command: str, *, timeout: int = 30) -> CursorGatewayResult:
        return CursorGatewayResult(status="success", text="", method="run")


def get_cursor_gateway(
    cwd: Path | str, *, dry_run: bool = False
) -> CursorGateway:
    """Factory — returns DryRun / CliCursorGateway.

    dry_run=True -> DryRunCursorGateway: empty/dry results, no Cursor SDK call.
    otherwise -> CliCursorGateway: real Cursor SDK (only if CURSOR_API_KEY is set).
    """
    if dry_run:
        return DryRunCursorGateway(cwd)
    return CliCursorGateway(cwd, dry_run=False)
