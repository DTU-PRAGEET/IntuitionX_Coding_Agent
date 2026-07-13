# `src/ui/` — Chainlit Web UI

## Purpose

This directory provides the **interactive chat UI** for the Jira Solver. Developers and reviewers submit Jira ticket IDs, watch pipeline progress, and approve/reject at human gates (Gate 1 research review, Gate 2 plan review).

## Start the UI

```bash
chainlit run src/ui/chainlit_app.py --port 8080
```

A root shim `chainlit_app.py` re-exports this module for backward compatibility with docker-compose.

## What this directory does

1. Parses user input (ticket ID or `mock` prefix)
2. Routes to production graph or mock linear pipeline
3. Streams agent progress as chat messages
4. Pauses at HITL gates with Approve / Revise / Reject buttons
5. Resumes the LangGraph via `Command(resume=...)`
6. Shows Jira ticket preview before running

## Files

| File | Role |
|------|------|
| `chainlit_app.py` | Main UI: chat handlers, mock vs graph routing, HITL resume, Jira preview |
| `__init__.py` | Package marker |

## Two user-facing paths

| Path | Trigger | Engine | Gates |
|------|---------|--------|-------|
| **Mock demo** | Input starts with `mock`, e.g. `mock SEC-4421` | [`src/pipeline/runner.py`](../pipeline/runner.py) | None — runs straight through |
| **Production** | Real ticket ID, e.g. `SEC-4421` | [`src/core/graph.py`](../core/graph.py) via `build_graph()` | Gate 1, Gate 2, or micro-confirm |

On startup, Chainlit imports `build_graph()` — all four agent modules load immediately.

## How to use the UI

### Production run

1. Type a Jira ticket ID (e.g. `PLAT-1234`)
2. UI fetches ticket preview from Jira
3. Pipeline runs: orchestrator → research → **pauses at Gate 1**
4. Review research summary; click **Approve**, **Revise** (with feedback), or **Reject**
5. Planner runs → **pauses at Gate 2** (or micro-confirm for MICRO route)
6. Review execution plan; approve again
7. Executor runs → PR link shown in chat

### Mock run

Type `mock SEC-4421` to run the linear demo path without gates. Resets the SQLi demo fixture in the target repo.

## How to interpret UI output

**Progress messages** map to pipeline stages:
- "Fetching Jira ticket…" → orchestrator
- "Running research…" → research agent tool loop
- "Generating plan…" → planner Cursor ask
- "Executing plan…" → executor worktree + edits

**Gate prompts** are formatted by [`src/core/formatters.py`](../core/formatters.py):
- Gate 1 shows `research_output` summary, hypotheses, affected files
- Gate 2 shows `plan_output` steps, files to change, risk level

**Final output:**
- Success → GitHub PR URL from `execution_output.mr_url`
- Failure → `exit_message` with stage and error

## Alternate entry: REST API

For CI/CD and automation, use [`src/api/`](../api/) instead of Chainlit. Same graph, same gates — but approval via `POST /approve/{thread_id}` instead of UI buttons.

## Related documentation

| Doc | Content |
|-----|---------|
| [`docs/CODEBASE_MAP.md`](../../docs/CODEBASE_MAP.md) | How the app runs — mermaid flowchart |
| [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) | What each stage does |
| [`src/pipeline/README.md`](../pipeline/README.md) | Graph runner vs mock runner |
| [`src/api/README.md`](../api/README.md) | REST API alternative |
