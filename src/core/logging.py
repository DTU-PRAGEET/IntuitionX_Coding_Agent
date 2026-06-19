"""Structured logging — [component_name] prefixes for terminal visibility."""

from __future__ import annotations

import logging
import sys

import structlog

_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog once per process."""
    global _configured
    if _configured:
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _prefix_component,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _configured = True


def _prefix_component(_logger: logging.Logger, _method: str, event_dict: dict) -> dict:
    component = event_dict.pop("component", "app")
    event = event_dict.pop("event", "")
    event_dict["event"] = f"[{component}] {event}"
    return event_dict


def get_logger(component_name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger tagged with component_name for [component] prefixes."""
    configure_logging()
    return structlog.get_logger().bind(component=component_name)
