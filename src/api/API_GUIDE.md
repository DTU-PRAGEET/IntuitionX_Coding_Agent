# Jira Solver — REST API Guide

Run the full Research → Planner → Executor pipeline over HTTP with Human-in-the-Loop (HITL) gates, progress events, and readable approval prompts.

## Start the API server

```bash
# From project root (with venv active)
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Health check:

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

**Requirements:** Redis running (`REDIS_URL` in `.env`, default `redis://localhost:6379`).

---

## Important: always send JSON

`curl -d '...'` without a content type sends form data and returns **422**. Always include:

```bash
-H "Content-Type: application/json"
```

---

## IDs you need

| ID | Where you get it | Used for |
|----|------------------|----------|
| `ticket_id` | Jira key, e.g. `SPACE-129070` | `POST /run`, `GET /tickets/{ticket_id}` |
| `job_id` | `POST /run` response | Event stream/poll: `/jobs/{job_id}/events` |
| `thread_id` | `POST /run` response or status | HITL approve: `POST /approve/{thread_id}` |

One ticket → one active job at a time. `job_id` and `thread_id` stay the same for the whole run (including across approve/resume).

---

## End-to-end workflow

### 1. Start a pipeline run

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"SPACE-129070","user_prompt":"optional extra context"}' \
  | python3 -m json.tool
```

Example response:

```json
{
  "job_id": "5b4a63a9-fb63-4da8-8832-395d0b37ac25",
  "ticket_id": "SPACE-129070",
  "status": "queued",
  "thread_id": "f67cd443-f439-4cfe-ad0f-3c6e4e4e0c4",
  "message": "Ticket queued for processing"
}
```

Save **`job_id`** and **`thread_id`**.

Optional body fields:

- `user_prompt` — extra instructions for agents
- `repo_path` — override repo path (default: resolved from Jira ticket)

---

### 2. Watch progress (choose one)

#### Option A — Plain-text pause view (best for curl)

When the job pauses for approval:

```bash
curl "http://localhost:8000/tickets/SPACE-129070/pause"
```

Returns **human-readable text**: hypothesis, affected files, and approve instructions.

#### Option B — SSE event stream (real-time)

Open in a **second terminal** right after `POST /run`:

```bash
curl -N "http://localhost:8000/jobs/<job_id>/events/stream?after_seq=0"
```

Event types you will see:

| Event | Meaning |
|-------|---------|
| `job_queued` | Job accepted |
| `job_running` | Worker started graph |
| `orchestrator_complete` | Routing decided |
| `research_complete` | Research report ready (JSON in payload) |
| `waiting_for_human_approval` | Paused at Gate 1 or Gate 2 |
| `planner_complete` | Execution plan ready |
| `executor_complete` | Executor finished |
| `execution_completed` / `execution_failed` | Terminal |

On HITL pause, SSE also emits an `approval_required` event with plain-text lines before the JSON event.

Reconnect after disconnect: `?after_seq=<last_seq_seen>`.

#### Option C — Poll events (JSON)

```bash
curl "http://localhost:8000/jobs/<job_id>/events?after_seq=0" | python3 -m json.tool
```

#### Option D — Poll job status

```bash
curl "http://localhost:8000/tickets/SPACE-129070" | python3 -m json.tool
```

When `status` is `"paused"`, useful fields:

- `display_message` — human-readable gate text (read this first)
- `pause_context` — full gate payload (`message`, `research_output`, `plan_output`)
- `paused_gate` — `GATE_1`, `GATE_2`, or `MICRO_CONFIRM`
- `thread_id` — for approve

Print only the readable message:

```bash
curl -s "http://localhost:8000/tickets/SPACE-129070" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('display_message','Not paused'))"
```

---

### 3. Approve, reject, or revise

After reading the report at `/pause` or `display_message`:

```bash
# Approve — continue to next phase
curl -X POST "http://localhost:8000/approve/<thread_id>" \
  -H "Content-Type: application/json" \
  -d '{"status":"APPROVE"}'

# Reject — stop the pipeline
curl -X POST "http://localhost:8000/approve/<thread_id>" \
  -H "Content-Type: application/json" \
  -d '{"status":"REJECT","feedback":"optional reason"}'

# Revise — re-run research or planner with feedback
curl -X POST "http://localhost:8000/approve/<thread_id>" \
  -H "Content-Type: application/json" \
  -d '{"status":"REVISE","feedback":"Re-check Dockerfile ARG defaults"}'
```

A **FULL** route typically needs **two approvals**:

1. **Gate 1** — after research (`GATE_1`)
2. **Gate 2** — after planner (`GATE_2`)

`thread_id` stays the same; call approve again when the second pause happens.

---

### 4. Check final result

```bash
curl "http://localhost:8000/tickets/SPACE-129070" | python3 -m json.tool
```

When done:

- `status`: `"completed"` or `"failed"`
- `mr_url`: merge request URL (if executor created one)
- `error`: failure message (if failed)

---

## Fetching reports

### Research report (Gate 1)

**Readable (recommended):**

```bash
curl "http://localhost:8000/tickets/SPACE-129070/pause"
```

**JSON:**

```bash
curl -s "http://localhost:8000/tickets/SPACE-129070" \
  | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('pause_context',{}).get('research_output'), indent=2))"
```

Or from events — look for `research_complete` or `waiting_for_human_approval` → `payload.research_output`.

### Execution plan (Gate 2)

When paused at Gate 2:

```bash
curl "http://localhost:8000/tickets/SPACE-129070/pause"
```

Plan JSON: `pause_context.plan_output` in status, or `planner_complete` event.

### Executor result

When `status` is `completed`:

- Status: `mr_url`, `routing_route`
- Events: `executor_complete` → `payload.execution_output`

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness |
| `POST` | `/run` | Start pipeline for a ticket |
| `GET` | `/tickets/{ticket_or_job_id}` | Job status + `display_message` when paused |
| `DELETE` | `/tickets/{ticket_or_job_id}` | **Cancel** active job and release ticket lock |
| `GET` | `/tickets/{ticket_or_job_id}/pause` | **Plain-text** HITL prompt + approve hints |
| `GET` | `/jobs/{job_id}/events` | Poll ordered events (JSON) |
| `GET` | `/jobs/{job_id}/events/stream` | SSE event stream |
| `POST` | `/approve/{thread_id}` | Resume after HITL gate |
| `GET` | `/dashboard/tokens` | Token usage + Cursor request counts (API key) |
| `GET` | `/dashboard/runs` | Pipeline run started/completed/failed + success rate (API key) |
| `GET` | `/dashboard/hag` | HAG approve/reject/revise counts (API key) |
| `GET` | `/dashboard/health` | In-flight and stuck jobs (API key) |

---

## Admin dashboard (observability)

JSON-only admin metrics backed by Redis daily counters (90-day TTL). All four routes require the `X-API-Key` header.

### Setup

1. Set a secret in `.env` / `.env.local` / `.env.production` (you invent this value — it is not issued by any service):

```bash
DASHBOARD_API_KEY=your-long-random-secret
DASHBOARD_STUCK_JOB_MINUTES=30   # optional; default 30
```

Generate one if needed:

```bash
openssl rand -hex 32
```

2. Restart the API so settings reload.

3. Call with the header:

```bash
curl -s -H "X-API-Key: $DASHBOARD_API_KEY" \
  "http://localhost:8000/dashboard/tokens?period=day" | python3 -m json.tool
```

If `DASHBOARD_API_KEY` is empty or the header is missing/wrong → **401**.

### Query parameters (tokens / runs / hag)

| Param | Values | Default |
|-------|--------|---------|
| `period` | `day` \| `week` \| `month` | `day` |
| `date` | `YYYY-MM-DD` (UTC anchor) | today (UTC) |

- **day** — that calendar date
- **week** — Monday–Sunday containing `date`
- **month** — calendar month containing `date`

### `GET /dashboard/tokens`

LLM token totals (by agent) and Cursor CLI **request** counts (Cursor has no token numbers).

```bash
curl -s -H "X-API-Key: $DASHBOARD_API_KEY" \
  "http://localhost:8000/dashboard/tokens?period=week&date=2026-07-21" | python3 -m json.tool
```

Example response:

```json
{
  "period": "week",
  "start_date": "2026-07-20",
  "end_date": "2026-07-26",
  "total_tokens": 12500,
  "by_agent": {
    "orchestrator": 800,
    "research": 6000,
    "planner": 3200,
    "executor": 2500
  },
  "cursor_requests": 14
}
```

### `GET /dashboard/runs`

```bash
curl -s -H "X-API-Key: $DASHBOARD_API_KEY" \
  "http://localhost:8000/dashboard/runs?period=month" | python3 -m json.tool
```

Example response:

```json
{
  "period": "month",
  "start_date": "2026-07-01",
  "end_date": "2026-07-31",
  "started": 40,
  "completed": 28,
  "failed": 7,
  "success_rate": 0.8,
  "note": "Cancelled runs are excluded from failed counts and success_rate."
}
```

`success_rate = completed / (completed + failed)`. Cancelled jobs are **not** counted as failed (intentional v1 blind spot).

### `GET /dashboard/hag`

Human approval gate decisions for research and planning: `approved`, `rejected`, `revised`.

```bash
curl -s -H "X-API-Key: $DASHBOARD_API_KEY" \
  "http://localhost:8000/dashboard/hag?period=day" | python3 -m json.tool
```

Example response:

```json
{
  "period": "day",
  "start_date": "2026-07-21",
  "end_date": "2026-07-21",
  "gates": {
    "research": {"approved": 3, "rejected": 0, "revised": 1},
    "planning": {"approved": 2, "rejected": 1, "revised": 0}
  }
}
```

### `GET /dashboard/health`

In-flight jobs (`queued` / `running` / `paused`) and stuck jobs (`queued` / `running` older than `DASHBOARD_STUCK_JOB_MINUTES`). Paused jobs waiting on a human are **not** treated as stuck.

```bash
curl -s -H "X-API-Key: $DASHBOARD_API_KEY" \
  "http://localhost:8000/dashboard/health" | python3 -m json.tool
```

---

## Common errors

### 422 Unprocessable Entity on `POST /run`

**Cause:** Missing `Content-Type: application/json`.

**Fix:**

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"SPACE-129070"}'
```

---

### 409 Conflict — "Active job already exists"

**Cause:** This ticket already has a job in `queued`, `running`, or **`paused`** state. You cannot start a new run until the current one finishes or you cancel it.

Example response (your case):

```json
{
  "detail": {
    "detail": "Active job already exists for SPACE-129070",
    "job_id": "5b4a63a9-fb63-4da8-8832-395d0b37ac25",
    "ticket_id": "SPACE-129070",
    "status": "paused",
    "thread_id": "f67cd443-f439-4cfe-ad0f-3c6ac9e4e0c4"
  }
}
```

**What to do (do not run `/run` again until cleared):**

**Option A — Cancel and restart (recommended):**

```bash
# Cancel the active job (works for queued or paused)
curl -X DELETE "http://localhost:8000/tickets/SPACE-129070" | python3 -m json.tool

# Start fresh
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"SPACE-129070"}'
```

**Option B — Continue the existing run:**

1. Read the research report:

   ```bash
   curl "http://localhost:8000/tickets/SPACE-129070/pause"
   ```

2. Approve, reject, or revise:

   ```bash
   curl -X POST "http://localhost:8000/approve/f67cd443-f439-4cfe-ad0f-3c6ac9e4e0c4" \
     -H "Content-Type: application/json" \
     -d '{"status":"APPROVE"}'
   ```

The 409 response includes `thread_id` and `job_id` so you can continue or cancel the existing run.

---

### 404 on `/jobs/{job_id}/events`

**Cause:** Invalid `job_id`, or wrong Redis instance after server restart without persisted jobs.

**Fix:** Use `job_id` from `POST /run` or `GET /tickets/{ticket_id}`.

---

### 409 on `/tickets/{id}/pause`

**Cause:** Job is not paused (still `running` or already `completed`).

**Fix:** Wait for research to finish, or check status with `GET /tickets/{id}`.

---

### 401 on `/dashboard/*`

**Cause:** Missing `X-API-Key` header, wrong key, or `DASHBOARD_API_KEY` not set in the server env.

**Fix:**

```bash
# In .env / .env.local
DASHBOARD_API_KEY=your-long-random-secret

# Then:
curl -H "X-API-Key: your-long-random-secret" \
  "http://localhost:8000/dashboard/tokens?period=day"
```

Restart uvicorn after changing env vars.

---

## Cancel or restart a run

Cancel an active job (`queued`, `paused`, or stale `running`) so you can `POST /run` again:

```bash
# By ticket id
curl -X DELETE "http://localhost:8000/tickets/SPACE-129070" | python3 -m json.tool

# Or by job_id
curl -X DELETE "http://localhost:8000/tickets/5b4a63a9-fb63-4da8-8832-395d0b37ac25" | python3 -m json.tool
```

Example response:

```json
{
  "job_id": "5b4a63a9-fb63-4da8-8832-395d0b37ac25",
  "ticket_id": "SPACE-129070",
  "status": "cancelled",
  "message": "Job cancelled for SPACE-129070; you can POST /run to start fresh"
}
```

Then start a new run:

```bash
curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"SPACE-129070"}' | python3 -m json.tool
```

**Notes:**

- Works for `queued` and `paused` jobs (your common case).
- If status is `running` and the worker is actively executing, cancel returns **409** — wait until the job pauses or fails.
- Cancelled jobs emit a `job_cancelled` event on the event stream.

---

## Quick copy-paste session

```bash
# 1. Start
RESP=$(curl -s -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"SPACE-129070"}')
echo "$RESP" | python3 -m json.tool

JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
THREAD_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['thread_id'])")

# 2. Stream progress (optional, separate terminal)
curl -N "http://localhost:8000/jobs/$JOB_ID/events/stream?after_seq=0"

# 3. When paused — read report
curl "http://localhost:8000/tickets/SPACE-129070/pause"

# 4. Approve Gate 1
curl -X POST "http://localhost:8000/approve/$THREAD_ID" \
  -H "Content-Type: application/json" \
  -d '{"status":"APPROVE"}'

# 5. When paused again at Gate 2 — read plan, approve again
curl "http://localhost:8000/tickets/SPACE-129070/pause"
curl -X POST "http://localhost:8000/approve/$THREAD_ID" \
  -H "Content-Type: application/json" \
  -d '{"status":"APPROVE"}'

# 6. Final status
curl "http://localhost:8000/tickets/SPACE-129070" | python3 -m json.tool
```

---

## Progress vs status

| Need | Endpoint |
|------|----------|
| Gate text to review | `GET /tickets/{id}/pause` or `display_message` |
| Approve / revise / reject | `POST /approve/{thread_id}` |
| Live progress | `GET /jobs/{job_id}/events/stream` |
| Final MR link | `GET /tickets/{id}` → `mr_url` |
| Token / Cursor usage | `GET /dashboard/tokens` (`X-API-Key`) |
| Run success rate | `GET /dashboard/runs` (`X-API-Key`) |
| HAG decisions | `GET /dashboard/hag` (`X-API-Key`) |
| Stuck / in-flight jobs | `GET /dashboard/health` (`X-API-Key`) |

The API does not push to your terminal automatically — you poll `/pause`, stream events, or check status while the worker runs in the background.
