# `src/api/` — REST API Server

## Purpose

This directory exposes the Jira Solver pipeline over **HTTP** for CI/CD integration, webhook-driven runs, and programmatic HITL (Human-in-the-Loop) approval. It is an alternate entry point to the Chainlit UI in [`src/ui/`](../ui/).

**Start the server:**

```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Docker runs this by default (`docker/Dockerfile`).

## What this directory does

1. Accepts a Jira ticket ID via `POST /run`
2. Enqueues a background job that runs the LangGraph pipeline until pause or completion
3. Streams progress events via Redis
4. Pauses at Gate 1 / Gate 2 / micro-confirm and waits for `POST /approve/{thread_id}`
5. Returns final execution output (MR URL, diff) on completion

## File layout

| File | Role |
|------|------|
| `main.py` | FastAPI app: routes (`/health`, `/run`, `/approve`, `/jobs`, `/tickets`) |
| `ticket_jobs.py` | Job queue: enqueue, run graph, resume after approval |
| `graph_runner.py` | *(imported from `src/pipeline/`)* — `run_graph_until_pause()`, `resume_graph_until_pause()` |
| `redis_store.py` | Redis-backed job state, event pub/sub |
| `execution_events.py` | Progress event types and serialization |
| `pause_display.py` | Human-readable gate pause messages for curl/API clients |
| `schemas.py` | Pydantic request/response models |
| `API_GUIDE.md` | **Full API reference** — curl examples, workflow, troubleshooting |
| `__init__.py` | Package marker |

## Key IDs (how to read API responses)

| ID | Where you get it | Used for |
|----|------------------|----------|
| `ticket_id` | Your input, e.g. `SEC-4421` | `POST /run`, `GET /tickets/{ticket_id}` |
| `job_id` | `POST /run` response | `GET /jobs/{job_id}/events` — progress stream |
| `thread_id` | `POST /run` response | `POST /approve/{thread_id}` — HITL resume |

One ticket → one active job. `job_id` and `thread_id` stay the same across approve/resume cycles.

## Typical workflow

```
POST /run {"ticket_id": "SEC-4421"}
    → {"job_id": "...", "thread_id": "...", "status": "queued"}

GET /jobs/{job_id}/events          # watch progress
    → orchestrator → research → [PAUSED at Gate 1]

POST /approve/{thread_id} {"status": "APPROVE"}
    → planner → [PAUSED at Gate 2]

POST /approve/{thread_id} {"status": "APPROVE"}
    → executor → {"status": "completed", "mr_url": "..."}
```

**Approval statuses:** `APPROVE` | `REVISE` | `REJECT`

- `REVISE` re-runs the prior agent with your feedback injected
- `REJECT` ends the pipeline

## How to interpret results

### Job status values

| Status | Meaning |
|--------|---------|
| `queued` | Job accepted, not yet started |
| `running` | Pipeline in progress |
| `paused` | Waiting at a human gate — check `gate` field (`GATE_1`, `GATE_2`, `MICRO_CONFIRM`) |
| `completed` | Executor finished; check `execution_output.mr_url` |
| `failed` | Pipeline error — check `error` / `exit_message` |

### Event stream

Events are published to Redis and exposed via `/jobs/{job_id}/events`. Each event has a `stage` (`orchestrator`, `research`, `planner`, `executor`) and `message`. Use these to build progress UIs or log pipeline steps.

### Pause display

`pause_display.py` formats gate requests for terminal/curl clients. For Chainlit, the UI uses [`src/core/formatters.py`](../core/formatters.py) instead.

## Requirements

- **Redis** running (`REDIS_URL` in `.env`, default `redis://localhost:6379`)
- Same env vars as the main app: Jira credentials, `CURSOR_API_KEY`, LLM provider keys (see [`src/core/config.py`](../core/config.py))

## Related documentation

| Doc | Content |
|-----|---------|
| [`API_GUIDE.md`](API_GUIDE.md) | Complete curl reference, error codes, webhook setup |
| [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) | What each pipeline stage does |
| [`docs/jira_webhook_setup.md`](../../docs/jira_webhook_setup.md) | Jira webhook → API integration |
| [`src/pipeline/README.md`](../pipeline/README.md) | Graph runner that powers API jobs |
| [`src/ui/README.md`](../ui/README.md) | Chainlit UI — same graph, interactive gates |
