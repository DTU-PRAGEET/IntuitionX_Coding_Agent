"""Package entry point — smoke test imports and tool registry."""

from __future__ import annotations

import sys

from src.core.logging import configure_logging, get_logger

logger = get_logger("src")

PRIMITIVE_TOOLS = ("read", "write", "edit", "bash", "find", "grep", "ls")


def main() -> int:
    configure_logging()
    try:
        from src.tools.registry import ToolRegistry

        registry = ToolRegistry(".")
        names = registry.list_names()
        missing = [t for t in PRIMITIVE_TOOLS if t not in names]
        if missing:
            logger.error("missing_tools", missing=missing, registered=names)
            return 1
        logger.info("ok", message=f"OK — {len(names)} primitive tools registered", tools=names)
        registry.dispose()
        return 0
    except Exception as e:
        logger.error("import_failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
