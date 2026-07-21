# Cursor Integration Migration Notes

Research repository navigation now flows through **`CursorGateway.search()`** via the **`cursor_search`** LangChain tool. The Research Deep Agent still performs reasoning and emits **`ResearchReport`** / **`ResearchOutput`**.

## Architecture Summary

| Path | Entry | State |
|------|-------|-------|
| **Production LangGraph** | `research_node` → `ResearchAgent.run()` | `AgentState` + HITL gates |
| **SubAgent chain (M1.3)** | `CursorResearchSubAgent` → `ResearchAgent.run()` | `AgentContext` dataclass |

```
PipelineOrchestrator
  → OrchestratorSubAgent (routing)
  → CursorResearchSubAgent (adapter)
  → ResearchAgent (Deep Agent)
      → cursor_search → CursorGateway.search()
  → CursorPlanSubAgent
  → CursorImplementSubAgent (M1.4)
```

See also: [`src/agents/research/SEARCH_WORKFLOW.md`](../agents/research/SEARCH_WORKFLOW.md)

## CursorGateway

All Cursor communication goes through `src/integrations/cursor/gateway.py`:

| Method | Purpose | Research usage |
|--------|---------|----------------|
| `search()` | Structured repo search (JSON hits) | **Primary** — via `cursor_search` tool |
| `ask()` | Read-only exploration / JSON responses | Plan runner (one-shot) |
| `edit()` | File modifications | **Implement sub-agent** via `implement_runner.py` |
| `run()` | Shell commands | Future automation |

Implementations:

- `DryRunCursorGateway` — mock structured hits for tests / `--dry-run`
- `CliCursorGateway` — wraps `cursor-sdk` via `run_local_agent`
- `get_cursor_gateway(cwd, dry_run=)` — factory

**Rule:** No file under `src/agents/` or `src/core/` may import `cursor_sdk` or call `run_local_agent` directly.

## Research tool permissions

| Tool | Status |
|------|--------|
| `cursor_search` | **Research** — replaces read/grep/find/ls/bash for repo nav |
| `web_search` | **Research** — external CVE/docs |
| `repo_profile` | **Research** — layout typing |
| `read`, `grep`, `find`, `ls`, `bash` | **Planner / internal** — unchanged |
| `write`, `edit` | **Executor** — unchanged |

L1 primitive tool files are **not deleted**; Cursor uses them internally during search prompts.

## Phased migration status

1. **Done** — `ResearchAgent` unified entry; `cursor_search` tool; structured `CursorSearchResponse`
2. **Done (M1.4)** — `CursorImplementSubAgent` calls `gateway.edit()`; returns `PatchResult`
3. **Late** — Optional LangGraph `EXECUTOR_MODE=cursor` bridge for API path

## M1.4 Implement Agent Migration

### Reuse (unchanged)

| Component | Path | Role |
|-----------|------|------|
| Worktree isolation | `src/agents/executor/tools/git_worktree.py` | `create_worktree`, `show_diff`, `stage_commit_push` |
| Git diff helpers | `src/core/repo_snapshot.py` | Snapshot + per-file diff |
| MR creation | `src/agents/executor/tools/mr_create.py` | GitHub PR after commit |
| SubAgent pattern | `src/agents/planner/sub_agent.py` | Template for `CursorImplementSubAgent` |
| FixPlan schema | `src/core/schemas.py` | Planner output consumed by implement |
| Gateway factory | `get_cursor_gateway()` | Dry-run + CLI implementations |

### Replace (SubAgent chain only)

| Before | After |
|--------|-------|
| `StubImplementAgent` | `CursorImplementSubAgent` |
| `ToolRegistry.execute("edit")` | `CursorGateway.edit()` |
| `_llm_edit_fallback()` patch loop | Cursor handles edit reasoning |
| READ/EDIT/VERIFY step runner for FixPlan | Single edit instruction + post-edit validation |

### Preserve (LangGraph path)

- `src/agents/executor/agent.py` — L1 edit/write tools remain for HITL pipeline
- `EXECUTOR_MODE=legacy` (default) — no change to production LangGraph executor
- L1 primitive tools under `src/tools/` — Cursor uses them internally during prompts

### Implement flow

```
FixPlan + research_output
  → SkillRegistry.select(agent="implement")
  → build_edit_instruction()
  → create_worktree()
  → gateway.edit(instruction, files, fix_plan, metadata)
  → validate changed_files vs expected
  → show_diff() → PatchResult
```

**Rule:** Agents never import `cursor_sdk` or call `run_local_agent` directly — only `CursorGateway`.

## Skills Plugin System (M1.4)

Skills live under repo-root `skills/<module>/<skill-name>.md`. `SkillRegistry` discovers and selects skills at runtime. Adding a new `.md` file changes agent prompts without editing `core/` or `agents/`.

| Agent | Base skill | Supplemental |
|-------|------------|----------------|
| Research | `skills/research/research.md` | `skills/security/*.md` when triggers match |
| Planner | `skills/planner/planner.md` | fix-type matched security skills |
| Implement | `skills/executor/executor.md` | fix-type matched security skills |

Deep Agents research backend points at `skills/` root (see `deep_runner.py`).

## Components

| Reuse as-is | Migrated | Deprecated |
|-------------|----------|------------|
| `SubAgent`, `AgentContext`, LangGraph executor (legacy), Jira | `ResearchAgent`, `cursor_search`, `gateway.search()` | `run_cursor_research()` one-shot |
| L1 `ToolRegistry` (LangGraph executor) | `CursorResearchSubAgent`, `CursorImplementSubAgent` | Per-agent `src/agents/*/skills/` (→ `skills/`) |
| Worktree + git + MR | `implement_runner.py`, `PatchResult` | `StubImplementAgent` |
