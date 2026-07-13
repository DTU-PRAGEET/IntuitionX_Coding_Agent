# `src/integrations/` — External Service Clients

## Purpose

This directory wraps **third-party services** the pipeline depends on: Cursor SDK (repo search, planning, edits), Jira (ticket fetch), and the Sprinklr LLM router. Agents never call these APIs directly — they go through the abstractions here.

## Directory layout

```
src/integrations/
├── cursor/       # Cursor SDK — production path for search, plan, edit
├── jira/         # Jira REST client (UI preview + shared helpers)
└── llm/          # Sprinklr internal LLM router
```

---

## `cursor/` — Cursor SDK (most important)

**Job:** Run headless Cursor agents for repository operations. This is the production path for research search, planner plan generation, and executor file edits.

| File | Role |
|------|------|
| `gateway.py` | `CursorGateway` ABC, `CliCursorGateway`, `DryRunCursorGateway` — factory `get_cursor_gateway()` |
| `client.py` | `run_local_agent()` — cursor-sdk entry; model from `CURSOR_MODEL` |
| `schemas.py` | `CursorSearchResponse`, `CursorEditResult` models |
| `semantic_plan_runner.py` | Planner: `run_semantic_plan()` → `gateway.ask()` |
| `plan_runner.py` | M1.3 alternate planner path (tests only) |
| `implement_runner.py` | M1.3 alternate implement path (tests only) |
| `research_runner.py` | Mock research helpers for dry-run |
| `README.md` | Setup (`CURSOR_API_KEY`), search workflow summary |
| `MIGRATION.md` | SDK migration notes |

### Gateway methods

| Method | Mode | Used by |
|--------|------|---------|
| `search(query, path)` | Read-only repo search | Research `cursor_search` tool |
| `ask(prompt)` | Ask mode — explore, no edits | Planner `run_semantic_plan()` |
| `edit(instruction, files)` | Agent mode — apply edits | Executor batch edit phase |

All methods call `run_local_agent()` in `client.py` with model **`CURSOR_MODEL`** (default `composer-2.5`).

### How to read Cursor results

**Search response** (`CursorSearchResponse`):
```json
{
  "query": "...",
  "hits": [{"path": "src/foo.py", "confidence": "high", "summary": "...", "snippets": ["..."]}],
  "flags": []
}
```

**Edit result** (`CursorEditResult`): check `changed_files`, `text` (agent summary), and any `flags`.

**Dry-run:** Without `CURSOR_API_KEY`, `DryRunCursorGateway` returns mock data — useful for wiring tests only.

**Related:** [`src/agents/research/SEARCH_WORKFLOW.md`](../agents/research/SEARCH_WORKFLOW.md)

---

## `jira/`

**Job:** Fetch and preview Jira tickets for the Chainlit UI.

| File | Role |
|------|------|
| `client.py` | Wraps orchestrator Jira helpers for UI ticket preview |

Low-level Jira SDK logic lives in [`src/agents/orchestrator/tools/`](../agents/orchestrator/tools/) (`jira_client.py`, `jira_schema.py`). This integration module is the UI-facing wrapper.

---

## `llm/`

**Job:** Sprinklr internal LLM router when `LLM_PROVIDER=sprinklr`.

| File | Role |
|------|------|
| `sprinklr_router.py` | Routes to internal GPT endpoint |

General LLM factory is in [`src/core/llm.py`](../core/llm.py) — this module is provider-specific.

---

## Environment variables

| Variable | Affects |
|----------|---------|
| `CURSOR_API_KEY` | Whether real Cursor runs (required for production) |
| `CURSOR_MODEL` | Cursor agent model (default `composer-2.5`) |
| `CURSOR_MAX_EDIT_ATTEMPTS` | Executor edit retries (default `2`) |
| `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | Jira fetch |
| `LLM_PROVIDER` | Which LLM backend `get_llm()` uses |

## Who calls what (quick reference)

| Phase | Integration | Method |
|-------|-------------|--------|
| Research search | `cursor/gateway.py` | `search()` |
| Planner plan | `cursor/semantic_plan_runner.py` | `gateway.ask()` |
| Executor edits | `cursor/gateway.py` | `edit()` |
| Orchestrator routing | `core/llm.py` | `get_llm(MEDIUM)` |
| Research loop | `core/llm.py` | `get_llm(HIGH)` |
| Executor validation | `core/llm.py` | `get_llm(LOW)` |
| Jira fetch | `orchestrator/tools/jira.py` | `fetch_jira_ticket()` |

## Related documentation

| Doc | Content |
|-----|---------|
| [`cursor/README.md`](cursor/README.md) | Cursor SDK setup |
| [`cursor/MIGRATION.md`](cursor/MIGRATION.md) | Migration from older integration |
| [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) | §10 LLM vs Cursor table |
| [`docs/CODEBASE_MAP.md`](../../docs/CODEBASE_MAP.md) | Integration file classification |
