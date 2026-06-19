"""LangGraph checkpointing — MongoDB with SQLite fallback."""

from __future__ import annotations

import os


def get_checkpointer():
    """Return a LangGraph checkpointer (Mongo preferred, SQLite fallback)."""
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
        from pathlib import Path

        db_path = Path(__file__).resolve().parents[2] / "data" / "checkpoints.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
