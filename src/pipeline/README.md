# `src/pipeline/` — Pipeline Runners

## Purpose

This directory contains **entry-point runners** that execute the agent pipeline outside of individual agent CLIs. Two paths exist:

1. **Production graph path** — LangGraph with HITL gates (used by API and Chainlit)
2. **Mock linear path** — no gates, for demos and quick smoke tests

## What this directory does

| Runner | Trigger | Engine | Gates |
|--------|---------|--------|-------|
| `graph_runner.py` | API `POST /run`, Chainlit production | `build_graph().astream()` | Gate 1, Gate 2, micro-confirm |
| `runner.py` | Chainlit input starts with `mock` | Linear function calls | None |

## File layout

| File | Role |
|------|------|
| `graph_runner.py` | `run_graph_until_pause()`, `resume_graph_until_pause()` — API/Chainlit graph driver |
| `runner.py` | `run_pipeline()` — mock/linear path; also resets demo fixtures |
| `pipeline_event_factory.py` | Builds progress events for API event stream |
| `__main__.py` | CLI: `python -m src.pipeline TICKET /repo` |
| `__init__.py` | Package marker |

## Production path (`graph_runner.py`)

Used by [`src/api/ticket_jobs.py`](../api/ticket_jobs.py) and Chainlit.

```
run_graph_until_pause(ticket_id, ...)
    → initial_state()
    → build_graph()
    → astream until: complete | fail | interrupt (gate pause)
    → return { status, thread_id, gate?, execution_output? }

resume_graph_until_pause(thread_id, decision, feedback)
    → Command(resume=...) with human decision
    → continue astream
```

**How to interpret return values:**

| `status` | Meaning | Next action |
|----------|---------|-------------|
| `paused` | At a human gate | `POST /approve/{thread_id}` or Chainlit button |
| `completed` | Executor done | Check `execution_output.mr_url` |
| `failed` | Pipeline error | Check `exit_message` / `current_error_log` |

The `gate` field on pause tells you which gate: `GATE_1` (research review), `GATE_2` (plan review), or `MICRO_CONFIRM`.

## Mock path (`runner.py`)

Triggered when user input starts with `mock` (e.g. `mock SEC-4421` in Chainlit).

```
run_pipeline(ticket_id, repo_path)
    → orchestrator (no gate)
    → research (no gate)
    → planner (no gate)
    → executor (no gate)
    → return result dict
```

No LangGraph, no human approval. Useful for:
- Local demos without pausing
- Resetting the SQLi demo fixture in `fixtures/`
- Quick integration smoke tests

## Event stream (API)

`pipeline_event_factory.py` creates typed events (`PipelineEvent`) published to Redis during graph runs. The API exposes these via `GET /jobs/{job_id}/events`.

Each event has:
- `stage` — `orchestrator`, `research`, `planner`, `executor`
- `message` — human-readable progress text
- `timestamp`

## CLI usage

```bash
# Standalone pipeline (bypasses API/Chainlit)
python -m src.pipeline SEC-4421 /path/to/repo
```

## Related documentation

| Doc | Content |
|-----|---------|
| [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) | §2 How a run starts |
| [`src/core/graph.py`](../core/graph.py) | LangGraph definition |
| [`src/api/README.md`](../api/README.md) | REST API that calls `graph_runner` |
| [`src/ui/README.md`](../ui/README.md) | Chainlit UI — mock vs graph routing |
| [`docs/CODEBASE_MAP.md`](../../docs/CODEBASE_MAP.md) | Conditional runtime: when each runner executes |
