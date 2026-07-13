# `src/memory/` — Failed-Attempt Persistence

## Purpose

This directory stores **prior pipeline failures** so the orchestrator and planner can learn from retries. When the executor fails on a ticket, the failure is saved here; on the next run for the same ticket, agents receive `previous_attempts` in their context.

## What it does

```
Executor fails → save_failed_attempt() → Redis
Next run       → get_failed_attempts(ticket_id) → previous_attempts on AgentState
                                              → orchestrator + planner read retry summary
```

## Files

| File | Role |
|------|------|
| `cache.py` | `save_failed_attempt()`, `get_failed_attempts()` — Redis-backed store |
| `__init__.py` | Re-exports cache helpers |

## Data model

Failures are stored as `FailedAttempt` typed dicts (defined in [`src/core/state.py`](../core/state.py)):

| Field | Meaning |
|-------|---------|
| `ticket_id` | Jira key, e.g. `SEC-4421` |
| `stage` | Where it failed: `executor`, `planner`, etc. |
| `error` | Error message or validation failure summary |
| `plan_summary` | Snapshot of the plan that failed |
| `timestamp` | When the failure occurred |

Redis key: `failed:{ticket_id}` — JSON array of attempts, 30-day TTL.

## How to read / interpret

**In orchestrator context:** `get_failed_attempts()` is called in `orchestrator_node()`. The routing prompt includes a retry summary so the LLM knows this ticket has failed before.

**In planner context:** `retry_summary_for_planner(previous_attempts)` injects the last failure into the Cursor plan prompt — e.g. "previous attempt failed because VERIFY step could not find intent_marker".

**When failures are written:**
- Executor step failure in the graph path → `save_failed_attempt()` in `executor_node()`
- Not written on Gate REJECT (human decision, not agent failure)

## Requirements

- **Redis** must be running (`REDIS_URL`, default `redis://localhost:6379`)
- Same Redis instance as [`src/api/redis_store.py`](../api/redis_store.py)

If Redis is unavailable, `save_failed_attempt()` and `get_failed_attempts()` fail silently (return `[]`) — the pipeline continues without retry memory.

## Related documentation

| Doc | Content |
|-----|---------|
| [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) | `previous_attempts` in AgentState table |
| [`src/core/state.py`](../core/state.py) | `FailedAttempt` schema |
| [`src/agents/executor/agent.py`](../agents/executor/agent.py) | Where failures are saved |
