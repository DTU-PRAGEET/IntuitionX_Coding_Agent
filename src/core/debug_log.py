"""Optional agent debug hook — no-op stub for pipeline diagnostics."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_DEBUG_LOG = Path(__file__).resolve().parents[2] / ".cursor" / "debug-98ee44.log"
_SESSION_ID = "98ee44"


def agent_debug(
    tag: str,
    location: str,
    event: str,
    data: dict[str, Any] | None = None,
    *,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    """Append one NDJSON debug line for the active debug session."""
    try:
        payload = {
            "sessionId": _SESSION_ID,
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": event,
            "data": {"tag": tag, **(data or {})},
            "hypothesisId": hypothesis_id,
            "runId": run_id,
        }
        _DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass
