"""Redis-backed daily metrics for dashboard observability."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from src.api.redis_store import KEY_PREFIX, RedisCommands, create_redis_client, load_job
from src.core.config import settings
from src.core.logging import get_logger

_log = get_logger("metrics_store")

METRICS_PREFIX = f"{KEY_PREFIX}:metrics"
METRICS_TTL_SECONDS = 60 * 60 * 24 * 90  # 90 days

Period = Literal["day", "week", "month"]
TRACKED_AGENTS = ("orchestrator", "research", "planner", "executor")
HAG_GATES = ("research", "planning")
HAG_DECISIONS = ("approved", "rejected", "revised")

_HAG_STATUS_TO_DECISION: dict[str, str] = {
    "APPROVED": "approved",
    "REVISE": "revised",
    "REJECT": "rejected",
    "REJECTED": "rejected",
}

_redis: RedisCommands | None = None


def _get_redis() -> RedisCommands:
    global _redis
    if _redis is None:
        _redis = create_redis_client()
    return _redis


def set_redis_client(client: RedisCommands | None) -> None:
    """Override Redis client (tests). Pass None to reset to default."""
    global _redis
    _redis = client


def get_metrics_redis() -> RedisCommands:
    """Return the shared metrics Redis client."""
    return _get_redis()


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _metric_key(suffix: str, day: date | None = None) -> str:
    day_str = (day or _utc_today()).isoformat()
    return f"{METRICS_PREFIX}:{suffix}:{day_str}"


def _incr(suffix: str, amount: int = 1, day: date | None = None) -> None:
    if amount <= 0:
        return
    key = _metric_key(suffix, day)
    try:
        redis_client = _get_redis()
        if amount == 1:
            redis_client.incr(key)
        else:
            redis_client.incrby(key, amount)
        redis_client.expire(key, METRICS_TTL_SECONDS)
    except Exception as exc:
        _log.warning("metrics_incr_failed", suffix=suffix, amount=amount, error=str(exc))


def resolve_period_dates(
    period: Period,
    anchor: date | None = None,
) -> tuple[date, date, list[str]]:
    """Return (start, end, list of YYYY-MM-DD strings) for the period containing anchor."""
    anchor = anchor or _utc_today()
    if period == "day":
        start = end = anchor
    elif period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
    else:
        start = anchor.replace(day=1)
        last_day = monthrange(anchor.year, anchor.month)[1]
        end = anchor.replace(day=last_day)

    dates: list[str] = []
    current = start
    while current <= end:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return start, end, dates


def _sum_keys(suffix_template: str, dates: list[str]) -> int:
    keys = [f"{METRICS_PREFIX}:{suffix_template}:{d}" for d in dates]
    try:
        redis_client = _get_redis()
        values = redis_client.mget(keys)
    except Exception as exc:
        _log.warning("metrics_sum_failed", suffix=suffix_template, error=str(exc))
        return 0
    total = 0
    for raw in values:
        if raw is None:
            continue
        try:
            total += int(raw)
        except (TypeError, ValueError):
            continue
    return total


def record_token_usage(agent: str, total_tokens: int) -> None:
    """Persist LLM token usage for dashboard aggregation."""
    if total_tokens <= 0:
        return
    _incr("tokens:total", total_tokens)
    if agent:
        _incr(f"tokens:{agent}", total_tokens)


def record_cursor_request() -> None:
    """Increment daily Cursor CLI request count (no token data available)."""
    _incr("cursor_requests")


def record_run_started() -> None:
    _incr("runs:started")


def record_run_completed() -> None:
    _incr("runs:completed")


def record_run_failed() -> None:
    _incr("runs:failed")


def record_hag_decision(gate_type: str, status: str) -> None:
    """Record HAG gate decision using explicit allowlist (no catch-all)."""
    decision = _HAG_STATUS_TO_DECISION.get(status.upper())
    if decision is None:
        _log.warning(
            "hag_decision_skipped",
            gate_type=gate_type,
            status=status,
        )
        return
    gate = gate_type.lower()
    if gate not in HAG_GATES:
        _log.warning("hag_gate_unknown", gate_type=gate_type, status=status)
        return
    _incr(f"hag:{gate}:{decision}")


def aggregate_tokens(period: Period, anchor: date | None = None) -> dict[str, Any]:
    start, end, dates = resolve_period_dates(period, anchor)
    by_agent = {agent: _sum_keys(f"tokens:{agent}", dates) for agent in TRACKED_AGENTS}
    return {
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_tokens": _sum_keys("tokens:total", dates),
        "by_agent": by_agent,
        "cursor_requests": _sum_keys("cursor_requests", dates),
    }


def aggregate_runs(period: Period, anchor: date | None = None) -> dict[str, Any]:
    """Aggregate run counters.

    Known blind spot: cancelled jobs are excluded from failed counts and
    success_rate (completed / (completed + failed)).
    """
    start, end, dates = resolve_period_dates(period, anchor)
    started = _sum_keys("runs:started", dates)
    completed = _sum_keys("runs:completed", dates)
    failed = _sum_keys("runs:failed", dates)
    terminal = completed + failed
    success_rate = (completed / terminal) if terminal else 0.0
    return {
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "started": started,
        "completed": completed,
        "failed": failed,
        "success_rate": round(success_rate, 4),
        "note": "Cancelled runs are excluded from failed counts and success_rate.",
    }


def aggregate_hag(period: Period, anchor: date | None = None) -> dict[str, Any]:
    start, end, dates = resolve_period_dates(period, anchor)
    gates: dict[str, dict[str, int]] = {}
    for gate in HAG_GATES:
        gates[gate] = {
            decision: _sum_keys(f"hag:{gate}:{decision}", dates)
            for decision in HAG_DECISIONS
        }
    return {
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "gates": gates,
    }


def collect_health(redis_client: RedisCommands, stuck_minutes: int) -> dict[str, Any]:
    """Return in-flight job count and jobs stuck longer than threshold."""
    from src.api.redis_store import job_key, ticket_active_key
    from src.api.schemas import TicketJobStatus
    from src.api.ticket_jobs import TicketJob

    active_statuses = {
        TicketJobStatus.QUEUED.value,
        TicketJobStatus.RUNNING.value,
        TicketJobStatus.PAUSED.value,
    }
    stuck_statuses = {
        TicketJobStatus.QUEUED.value,
        TicketJobStatus.RUNNING.value,
    }
    threshold = datetime.now(timezone.utc) - timedelta(minutes=stuck_minutes)

    in_flight: list[dict[str, Any]] = []
    stuck: list[dict[str, Any]] = []

    try:
        active_keys = redis_client.keys(f"{KEY_PREFIX}:ticket_active:*")
    except Exception as exc:
        _log.warning("health_scan_failed", error=str(exc))
        return {
            "in_flight_count": 0,
            "stuck_count": 0,
            "stuck_threshold_minutes": stuck_minutes,
            "in_flight_jobs": [],
            "stuck_jobs": [],
            "error": str(exc),
        }

    seen_job_ids: set[str] = set()
    for key in active_keys:
        ticket_id = key.rsplit(":", 1)[-1]
        job_id = redis_client.get(ticket_active_key(ticket_id))
        if not job_id or job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)
        data = load_job(redis_client.get(job_key(job_id)))
        if not data:
            continue
        job = TicketJob.from_dict(data)
        if job.status.value not in active_statuses:
            continue
        entry = {
            "job_id": job.job_id,
            "ticket_id": job.ticket_id,
            "status": job.status.value,
            "updated_at": job.updated_at,
        }
        in_flight.append(entry)
        if job.status.value in stuck_statuses:
            try:
                updated = datetime.fromisoformat(job.updated_at.replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if updated < threshold:
                stuck.append(entry)

    return {
        "in_flight_count": len(in_flight),
        "stuck_count": len(stuck),
        "stuck_threshold_minutes": stuck_minutes,
        "in_flight_jobs": in_flight,
        "stuck_jobs": stuck,
    }
