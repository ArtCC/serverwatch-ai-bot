"""SQLite persistence layer using aiosqlite.

Exposes a simple key/value settings table and typed helpers for
the data the bot currently needs to persist.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_KEY_ACTIVE_MODEL = "active_model"


def _db_path() -> str:
    return get_config().sqlite_path


async def init_db() -> None:
    """Create schema and seed default values on first run."""
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await db.commit()

        async with db.execute(
            "SELECT value FROM settings WHERE key = ?", (_KEY_ACTIVE_MODEL,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            default_model = get_config().ollama_model
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                (_KEY_ACTIVE_MODEL, default_model),
            )
            await db.commit()
            logger.info("Seeded active_model = %s", default_model)


async def get_active_model() -> str:
    """Return the active Ollama model name stored in the DB."""
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE key = ?", (_KEY_ACTIVE_MODEL,)
        ) as cursor:
            row = await cursor.fetchone()

    if row is None:
        return get_config().ollama_model
    return str(row[0])


async def set_active_model(model: str) -> None:
    """Persist the selected Ollama model to settings."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_KEY_ACTIVE_MODEL, model),
        )
        await db.commit()
    logger.info("Active model set to %s", model)
