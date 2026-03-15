"""SQLite persistence layer using aiosqlite.

Exposes a simple key/value settings table and typed helpers for
the data the bot currently needs to persist.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_KEY_ACTIVE_MODEL = "active_model"
_KEY_CPU_THRESHOLD = "threshold_cpu"
_KEY_RAM_THRESHOLD = "threshold_ram"
_KEY_DISK_THRESHOLD = "threshold_disk"
_VALID_PROVIDERS = {"ollama", "openai", "anthropic", "deepseek"}
_db: aiosqlite.Connection | None = None
_db_lock = asyncio.Lock()
_db_init_lock = asyncio.Lock()


@dataclass(frozen=True)
class ContextUsage:
    used_chars: int
    max_chars: int
    used_messages: int
    max_messages: int
    stored_messages: int

    @property
    def used_tokens_estimate(self) -> int:
        return _estimate_tokens(self.used_chars)

    @property
    def max_tokens_estimate(self) -> int:
        return _estimate_tokens(self.max_chars)


def _estimate_tokens(chars: int) -> int:
    if chars <= 0:
        return 0
    return (chars + 3) // 4


def _trim_history_window(
    messages: list[tuple[str, str]],
    *,
    max_messages: int,
    max_chars: int,
) -> list[dict[str, str]]:
    if max_messages <= 0 or max_chars <= 0:
        return []

    candidates = messages[-max_messages:]
    selected_rev: list[tuple[str, str]] = []
    used_chars = 0

    for role, content in reversed(candidates):
        content_len = len(content)

        if not selected_rev and content_len > max_chars:
            clipped = content[-max_chars:]
            selected_rev.append((role, clipped))
            break

        if used_chars + content_len > max_chars:
            break

        selected_rev.append((role, content))
        used_chars += content_len

    return [
        {"role": role, "content": content} for role, content in reversed(selected_rev) if content
    ]


def normalize_model_selection(value: str) -> str:
    """Normalise model selections to provider:model format.

    Backwards compatibility: legacy plain model names are interpreted as
    Ollama models.
    """
    cleaned = value.strip()
    provider, sep, model = cleaned.partition(":")
    if sep and provider in _VALID_PROVIDERS and model.strip():
        return f"{provider}:{model.strip()}"
    return f"ollama:{cleaned}"


def split_model_selection(selection: str) -> tuple[str, str]:
    normalized = normalize_model_selection(selection)
    provider, _, model = normalized.partition(":")
    return provider, model


def _db_path() -> str:
    return get_config().sqlite_path


async def _get_db() -> aiosqlite.Connection:
    """Return a shared SQLite connection for the whole runtime."""
    global _db

    async with _db_init_lock:
        if _db is not None:
            return _db

        path = _db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _db = await aiosqlite.connect(path)
        return _db


async def close_db() -> None:
    """Close the shared SQLite connection on app shutdown."""
    global _db

    async with _db_lock:
        if _db is None:
            return
        await _db.close()
        _db = None


async def init_db() -> None:
    """Create schema and seed default values on first run."""
    cfg = get_config()

    db = await _get_db()
    async with _db_lock:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_context (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_context_chat_id_id
            ON chat_context(chat_id, id)
            """
        )
        await db.commit()

        defaults = {
            _KEY_ACTIVE_MODEL: normalize_model_selection(cfg.ollama_model),
            _KEY_CPU_THRESHOLD: str(cfg.alert_default_cpu_threshold),
            _KEY_RAM_THRESHOLD: str(cfg.alert_default_ram_threshold),
            _KEY_DISK_THRESHOLD: str(cfg.alert_default_disk_threshold),
        }
        for key, value in defaults.items():
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
                logger.info("Seeded %s = %s", key, value)

        await db.commit()


async def _get(key: str) -> str | None:
    async with _db_lock:
        db = await _get_db()
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
    return str(row[0]) if row else None


async def _set(key: str, value: str) -> None:
    async with _db_lock:
        db = await _get_db()
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def append_chat_context_message(chat_id: int, role: str, content: str) -> None:
    cleaned = content.strip()
    if role not in {"user", "assistant"} or not cleaned:
        return

    keep = max(1, get_config().chat_context_retention_messages)
    async with _db_lock:
        db = await _get_db()
        await db.execute(
            "INSERT INTO chat_context(chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, cleaned),
        )
        await db.execute(
            """
            DELETE FROM chat_context
            WHERE chat_id = ?
              AND id NOT IN (
                  SELECT id
                  FROM chat_context
                  WHERE chat_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (chat_id, chat_id, keep),
        )
        await db.commit()


async def get_chat_context_window(
    chat_id: int,
    *,
    max_turns: int,
    max_chars: int,
) -> list[dict[str, str]]:
    max_messages = max(0, max_turns) * 2
    if max_messages == 0 or max_chars <= 0:
        return []

    async with _db_lock:
        db = await _get_db()
        async with db.execute(
            "SELECT role, content FROM chat_context WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    messages = [(str(row[0]), str(row[1])) for row in rows]
    return _trim_history_window(messages, max_messages=max_messages, max_chars=max_chars)


async def get_chat_context_usage(
    chat_id: int,
    *,
    max_turns: int,
    max_chars: int,
) -> ContextUsage:
    max_messages = max(0, max_turns) * 2
    async with _db_lock:
        db = await _get_db()
        async with db.execute(
            "SELECT role, content FROM chat_context WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    messages = [(str(row[0]), str(row[1])) for row in rows]
    window = _trim_history_window(messages, max_messages=max_messages, max_chars=max_chars)
    return ContextUsage(
        used_chars=sum(len(item["content"]) for item in window),
        max_chars=max(0, max_chars),
        used_messages=len(window),
        max_messages=max_messages,
        stored_messages=len(messages),
    )


async def clear_chat_context(chat_id: int) -> None:
    async with _db_lock:
        db = await _get_db()
        await db.execute("DELETE FROM chat_context WHERE chat_id = ?", (chat_id,))
        await db.commit()


# ------------------------------------------------------------------
# Active model
# ------------------------------------------------------------------


async def get_active_model() -> str:
    value = await _get(_KEY_ACTIVE_MODEL)
    if value is None:
        return normalize_model_selection(get_config().ollama_model)
    return normalize_model_selection(value)


async def set_active_model(model: str) -> None:
    normalized = normalize_model_selection(model)
    await _set(_KEY_ACTIVE_MODEL, normalized)
    logger.info("Active model set to %s", normalized)


# ------------------------------------------------------------------
# Alert thresholds
# ------------------------------------------------------------------


async def get_thresholds() -> tuple[float, float, float]:
    """Return (cpu, ram, disk) thresholds in a single DB round-trip."""
    keys = (_KEY_CPU_THRESHOLD, _KEY_RAM_THRESHOLD, _KEY_DISK_THRESHOLD)
    async with _db_lock:
        db = await _get_db()
        async with db.execute(
            "SELECT key, value FROM settings WHERE key IN (?, ?, ?)", keys
        ) as cursor:
            rows = await cursor.fetchall()
    mapping: dict[str, str] = {str(row[0]): str(row[1]) for row in rows}
    cfg = get_config()
    cpu = (
        float(mapping[_KEY_CPU_THRESHOLD])
        if _KEY_CPU_THRESHOLD in mapping
        else cfg.alert_default_cpu_threshold
    )
    ram = (
        float(mapping[_KEY_RAM_THRESHOLD])
        if _KEY_RAM_THRESHOLD in mapping
        else cfg.alert_default_ram_threshold
    )
    disk = (
        float(mapping[_KEY_DISK_THRESHOLD])
        if _KEY_DISK_THRESHOLD in mapping
        else cfg.alert_default_disk_threshold
    )
    return cpu, ram, disk


async def get_threshold_cpu() -> float:
    value = await _get(_KEY_CPU_THRESHOLD)
    return float(value) if value is not None else get_config().alert_default_cpu_threshold


async def get_threshold_ram() -> float:
    value = await _get(_KEY_RAM_THRESHOLD)
    return float(value) if value is not None else get_config().alert_default_ram_threshold


async def get_threshold_disk() -> float:
    value = await _get(_KEY_DISK_THRESHOLD)
    return float(value) if value is not None else get_config().alert_default_disk_threshold


async def set_threshold_cpu(value: float) -> None:
    await _set(_KEY_CPU_THRESHOLD, str(value))
    logger.info("CPU threshold set to %s", value)


async def set_threshold_ram(value: float) -> None:
    await _set(_KEY_RAM_THRESHOLD, str(value))
    logger.info("RAM threshold set to %s", value)


async def set_threshold_disk(value: float) -> None:
    await _set(_KEY_DISK_THRESHOLD, str(value))
    logger.info("Disk threshold set to %s", value)
