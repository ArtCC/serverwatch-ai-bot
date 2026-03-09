from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services import scheduler


class _FakeBot:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, *, chat_id: int, text: str, parse_mode: str) -> None:
        self.messages.append(text)


class _FakeContext:
    def __init__(self) -> None:
        self.bot = _FakeBot()
        self.bot_data: dict[str, object] = {}


def test_health_alert_escapes_markdown_special_chars(monkeypatch) -> None:
    fake_snapshot = SimpleNamespace(
        cpu_percent=10.0,
        ram_percent=10.0,
        disk_percent=10.0,
        health_level="warning",
        health_score=70,
        key_findings=["name_with_underscore", "disk*high"],
        recommended_action="check [service] now",
    )

    class _FakeConfig:
        alert_cooldown_seconds = 0
        bot_locale = "en"
        telegram_chat_id = 1

    async def _fake_get_snapshot() -> object:
        return fake_snapshot

    async def _fake_get_thresholds() -> tuple[float, float, float]:
        return (95.0, 95.0, 95.0)

    monkeypatch.setattr(scheduler, "get_config", lambda: _FakeConfig())
    monkeypatch.setattr(scheduler.glances, "get_snapshot", _fake_get_snapshot)
    monkeypatch.setattr(scheduler.store, "get_thresholds", _fake_get_thresholds)

    ctx = _FakeContext()
    asyncio.run(scheduler.check_and_alert(ctx))

    assert ctx.bot.messages
    text = ctx.bot.messages[-1]
    assert "name\\_with\\_underscore" in text
    assert "disk\\*high" in text
    assert "check \\[service] now" in text
