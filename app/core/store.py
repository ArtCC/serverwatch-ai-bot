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
_KEY_CPU_THRESHOLD = "threshold_cpu"
_KEY_RAM_THRESHOLD = "threshold_ram"
_KEY_DISK_THRESHOLD = "threshold_disk"
_VALID_PROVIDERS = {"ollama", "openai", "anthropic", "deepseek"}


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


async def init_db() -> None:
    """Create schema and seed default values on first run."""
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cfg = get_config()

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
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
    return str(row[0]) if row else None


async def _set(key: str, value: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
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
