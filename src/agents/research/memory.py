"""In-memory research execution memory — swappable backend interface."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class MemoryEvent:
    """One append-only research memory row (event_type + payload + timestamp).
    Stored in `InMemoryResearchMemory`; serialized into prompts via `to_context_snippet`."""
    event_type: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ResearchMemoryStore(ABC):
    """Interface for per-run research memory (Redis substitution later)."""

    @abstractmethod
    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        """Append a typed observation to the research run memory."""
        ...

    @abstractmethod
    def get_events(self, event_type: Optional[str] = None) -> list[MemoryEvent]:
        """Return stored events, optionally filtered by event type."""
        ...

    @abstractmethod
    def to_context_snippet(self, max_events: int = 10) -> str:
        """Serialize recent memory events into prompt-friendly text."""
        ...


class InMemoryResearchMemory(ResearchMemoryStore):
    """Ephemeral memory scoped to a single Research Agent run."""

    def __init__(self) -> None:
        """Initialize InMemoryResearchMemory with the provided arguments."""
        self._events: list[MemoryEvent] = []

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        """Record for InMemoryResearchMemory."""
        self._events.append(MemoryEvent(event_type=event_type, payload=payload))

    def get_events(self, event_type: Optional[str] = None) -> list[MemoryEvent]:
        """Get Events for InMemoryResearchMemory."""
        if event_type is None:
            return list(self._events)
        return [e for e in self._events if e.event_type == event_type]

    def to_context_snippet(self, max_events: int = 10) -> str:
        """To Context Snippet for InMemoryResearchMemory."""
        if not self._events:
            return ""
        recent = self._events[-max_events:]
        lines = [
            f"- [{e.event_type}] {json.dumps(e.payload, default=str)[:300]}"
            for e in recent
        ]
        return "Run memory (recent observations):\n" + "\n".join(lines)

    def clear(self) -> None:
        """Clear for InMemoryResearchMemory."""
        self._events.clear()
