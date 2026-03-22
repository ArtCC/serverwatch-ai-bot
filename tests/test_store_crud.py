"""Tests for store CRUD with an in-memory SQLite database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import patch

import aiosqlite

from app.core import store
from app.core.config import Config

_TEST_CONFIG = Config(
    telegram_bot_token="tok",
    telegram_chat_id=1,
    glances_base_url="http://localhost:61208/api/4",
    glances_request_timeout_seconds=8.0,
    glances_log_full_payload=False,
    ollama_base_url="http://localhost:11434",
    ollama_model="llama3.2:3b",
    openai_api_key=None,
    openai_model=None,
    anthropic_api_key=None,
    anthropic_model=None,
    anthropic_max_tokens=2048,
    deepseek_api_key=None,
    deepseek_model=None,
    bot_log_level="INFO",
    bot_locale="en",
    sqlite_path=":memory:",
    alert_check_interval_seconds=60,
    alert_cooldown_seconds=300,
    alert_default_cpu_threshold=85.0,
    alert_default_ram_threshold=85.0,
    alert_default_disk_threshold=90.0,
    alert_consecutive_breaches=2,
    alert_recovery_margin_percent=5.0,
    alert_context_window_samples=3,
    chat_context_max_turns=8,
    chat_context_max_chars=10000,
    chat_context_retention_messages=200,
    tz="UTC",
)


@asynccontextmanager
async def _mem_store():
    """Provide a fresh :memory: SQLite wired into the store module."""
    conn = await aiosqlite.connect(":memory:")
    with (
        patch.object(store, "_db", conn),
        patch.object(store, "_db_path", return_value=":memory:"),
        patch("app.core.store.get_config", return_value=_TEST_CONFIG),
    ):
        await store.init_db()
        yield conn
    await conn.close()


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_default_thresholds_match_config() -> None:
    async def _test():
        async with _mem_store():
            cpu, ram, disk = await store.get_thresholds()
            assert cpu == 85.0
            assert ram == 85.0
            assert disk == 90.0

    _run(_test())


def test_set_and_get_thresholds() -> None:
    async def _test():
        async with _mem_store():
            await store.set_threshold_cpu(70.0)
            await store.set_threshold_ram(60.0)
            await store.set_threshold_disk(50.0)

            cpu, ram, disk = await store.get_thresholds()
            assert cpu == 70.0
            assert ram == 60.0
            assert disk == 50.0

    _run(_test())


def test_set_and_get_active_model() -> None:
    async def _test():
        async with _mem_store():
            await store.set_active_model("openai:gpt-4o-mini")
            result = await store.get_active_model()
            assert result == "openai:gpt-4o-mini"

    _run(_test())


def test_chat_context_round_trip() -> None:
    async def _test():
        async with _mem_store():
            chat_id = 12345
            await store.append_chat_context_message(chat_id, "user", "Hello")
            await store.append_chat_context_message(chat_id, "assistant", "Hi there")

            history = await store.get_chat_context_window(chat_id, max_turns=10, max_chars=5000)
            assert len(history) == 2
            assert history[0] == {"role": "user", "content": "Hello"}
            assert history[1] == {"role": "assistant", "content": "Hi there"}

    _run(_test())


def test_clear_chat_context() -> None:
    async def _test():
        async with _mem_store():
            chat_id = 99
            await store.append_chat_context_message(chat_id, "user", "test")
            await store.clear_chat_context(chat_id)

            history = await store.get_chat_context_window(chat_id, max_turns=10, max_chars=5000)
            assert history == []

    _run(_test())


def test_context_usage_counts() -> None:
    async def _test():
        async with _mem_store():
            chat_id = 42
            await store.append_chat_context_message(chat_id, "user", "abc")
            await store.append_chat_context_message(chat_id, "assistant", "de")

            usage = await store.get_chat_context_usage(chat_id, max_turns=10, max_chars=5000)
            assert usage.used_messages == 2
            assert usage.stored_messages == 2
            assert usage.used_chars == 5  # "abc" + "de"

    _run(_test())
