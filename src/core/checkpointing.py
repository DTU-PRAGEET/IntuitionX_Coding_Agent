"""LangGraph checkpointing — MongoDB with SQLite fallback."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ASYNC_CHECKPOINTER: Any | None = None
_ASYNC_SQLITE_CONN: Any | None = None

_CHECKPOINT_DB = Path(__file__).resolve().parents[2] / "data" / "checkpoints.db"


def get_checkpointer():
    """Return a sync LangGraph checkpointer (Mongo preferred, SQLite fallback)."""
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")

    try:
        from langgraph.checkpoint.mongodb import MongoDBSaver
        from pymongo import MongoClient

        client = MongoClient(mongo_url, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        return MongoDBSaver(client)
    except Exception:
        pass

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        import sqlite3

        _CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_CHECKPOINT_DB), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()


async def ensure_async_checkpointer():
    """Return an async-compatible checkpointer for graph.astream / ainvoke."""
    global _ASYNC_CHECKPOINTER, _ASYNC_SQLITE_CONN

    if _ASYNC_CHECKPOINTER is not None:
        return _ASYNC_CHECKPOINTER

    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        _CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
        _ASYNC_SQLITE_CONN = await aiosqlite.connect(str(_CHECKPOINT_DB), timeout=30)
        _ASYNC_CHECKPOINTER = AsyncSqliteSaver(_ASYNC_SQLITE_CONN)
        return _ASYNC_CHECKPOINTER
    except Exception:
        from langgraph.checkpoint.memory import MemorySaver

        _ASYNC_CHECKPOINTER = MemorySaver()
        return _ASYNC_CHECKPOINTER
