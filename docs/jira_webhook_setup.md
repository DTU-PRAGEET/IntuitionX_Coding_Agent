# Jira Webhook Setup

This document describes how to point a Jira Cloud (or Data Center) webhook at the Sprinklr Jira Solver FastAPI service.

## Start the API

```bash
export WEBHOOK_API_KEY="your-secret-key-here"
export REDIS_URL="redis://localhost:6379"
export WEBHOOK_REQUIRED_LABELS="auto-fix"
export WEBHOOK_ALLOWED_TRANSITIONS="Ready for Agent"
export WEBHOOK_MAX_CONCURRENCY=2

uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

Or via Docker Compose (port 8000 on the `app` service):

```bash
WEBHOOK_API_KEY=your-secret-key-here docker compose -f docker/docker-compose.yml up app redis
```

## Authentication

Every request to `POST /jira/webhook` must include:

```http
Authorization: Bearer <WEBHOOK_API_KEY>
```

Missing or invalid keys receive **401 Unauthorized**. If `WEBHOOK_API_KEY` is not set on the server, the endpoint returns **503 Service Unavailable**.

## Webhook filtering (before enqueue)

Events are filtered **in the webhook handler** — routine Jira edits do not trigger pipeline runs.

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEBHOOK_REQUIRED_LABELS` | `auto-fix` | Comma-separated labels; ticket must have at least one |
| `WEBHOOK_ALLOWED_TRANSITIONS` | `Ready for Agent` | For `issue_updated` without label: status must transition to one of these |

Rules:

- **`jira:issue_created`** — enqueued only if the issue has a required label.
- **`jira:issue_updated`** — enqueued if the issue has a required label **or** changelog shows status → allowed transition.
- Non-matching events return HTTP 200 with `{"status":"ignored","message":"…"}` (no enqueue).

Recommended Jira-side JQL on the webhook: `project = PLAT AND labels = auto-fix`.

## Duplicate tickets (409)

If a ticket already has a job in `queued`, `running`, or `paused` state, a new webhook returns **409 Conflict** with the existing `job_id`, `status`, and `thread_id`. Jira redeliveries therefore surface visibly instead of starting duplicate runs.

After a job reaches `completed` or `failed`, a new webhook for the same ticket is accepted (new `job_id`, new worktree path).

## Jira webhook configuration

1. In Jira: **Settings → System → WebHooks → Create webhook**
2. **URL:** `https://<your-host>:8000/jira/webhook`
3. **Events:** Issue → created, Issue → updated
4. **JQL filter (recommended):** `project = PLAT AND labels = auto-fix`

### Example payload (issue created)

```json
{
  "webhookEvent": "jira:issue_created",
  "issue": {
    "key": "PLAT-1234",
    "fields": {
      "summary": "Fix SQL injection in login",
      "labels": ["auto-fix"],
      "status": { "name": "Open" }
    }
  }
}
```

### Test without Jira

```bash
curl -s -X POST http://localhost:8000/jira/webhook \
  -H "Authorization: Bearer your-secret-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "webhookEvent": "jira:issue_created",
    "issue": {
      "key": "PLAT-1234",
      "fields": { "labels": ["auto-fix"] }
    }
  }'
```

Response (immediate — pipeline runs in background):

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "ticket_id": "PLAT-1234",
  "status": "queued",
  "message": "Ticket queued for processing (label_match)"
}
```

## HITL gates (webhook runs pause — not auto-approved)

Webhook-triggered runs use the **same LangGraph pipeline and HITL gates** as Chainlit. When a gate is reached, the job moves to `paused` and waits for:

```bash
curl -X POST "http://localhost:8000/approve/<thread_id>" \
  -H "Content-Type: application/json" \
  -d '{"status":"APPROVE","feedback":null}'
```

Use `REVISE` or `REJECT` with feedback the same way as in Chainlit. Chainlit is unchanged for interactive use.

## Status polling

```bash
curl http://localhost:8000/tickets/PLAT-1234
curl http://localhost:8000/tickets/<job_id>
```

Status flow: `queued` → `running` → `paused` (optional, at gates) → `running` → `completed` | `failed`.

Example paused response:

```json
{
  "job_id": "…",
  "ticket_id": "PLAT-1234",
  "status": "paused",
  "thread_id": "…",
  "paused_gate": "GATE_1",
  "routing_route": "FULL"
}
```

## Concurrency

- Up to **`WEBHOOK_MAX_CONCURRENCY`** (default **2**) pipeline runs execute in parallel inside the FastAPI process.
- Job queue and status are stored in **Redis** (survives API restarts).
- Each run gets an isolated git worktree at `{WORKTREE_BASE}/{ticket_id}_{job_id}`.

## Health check

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Environment

Ensure Jira credentials are set (`JIRA_SERVER_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`) so the orchestrator can fetch ticket details. Repo resolution follows the ticket GitHub link in Jira (same as Chainlit when no explicit repo path is given).
