"""Tests for Config __post_init__ range validation."""

import pytest

from app.core.config import Config

_VALID_KWARGS: dict[str, object] = {
    "telegram_bot_token": "token",
    "telegram_chat_id": 1,
    "glances_base_url": "http://glances:61208/api/4",
    "glances_request_timeout_seconds": 8.0,
    "glances_log_full_payload": False,
    "ollama_base_url": "http://host.docker.internal:11434",
    "ollama_model": "llama3.2:3b",
    "openai_api_key": None,
    "openai_model": None,
    "anthropic_api_key": None,
    "anthropic_model": None,
    "anthropic_max_tokens": 2048,
    "deepseek_api_key": None,
    "deepseek_model": None,
    "bot_log_level": "INFO",
    "bot_locale": "en",
    "sqlite_path": ":memory:",
    "alert_check_interval_seconds": 60,
    "alert_cooldown_seconds": 300,
    "alert_default_cpu_threshold": 85.0,
    "alert_default_ram_threshold": 85.0,
    "alert_default_disk_threshold": 90.0,
    "alert_consecutive_breaches": 2,
    "alert_recovery_margin_percent": 5.0,
    "alert_context_window_samples": 3,
    "chat_context_max_turns": 8,
    "chat_context_max_chars": 10000,
    "chat_context_retention_messages": 200,
    "tz": "UTC",
}


def _make(**overrides: object) -> Config:
    return Config(**{**_VALID_KWARGS, **overrides})  # type: ignore[arg-type]


def test_valid_config_does_not_raise() -> None:
    _make()


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("glances_request_timeout_seconds", 0),
        ("glances_request_timeout_seconds", -1),
        ("alert_check_interval_seconds", -1),
        ("alert_cooldown_seconds", -1),
        ("alert_default_cpu_threshold", -1),
        ("alert_default_cpu_threshold", 101),
        ("alert_default_ram_threshold", -0.1),
        ("alert_default_ram_threshold", 100.1),
        ("alert_default_disk_threshold", -5),
        ("alert_default_disk_threshold", 200),
        ("alert_consecutive_breaches", 0),
        ("alert_recovery_margin_percent", -1),
        ("alert_recovery_margin_percent", 101),
        ("alert_context_window_samples", 0),
        ("chat_context_max_turns", -1),
        ("chat_context_max_chars", -1),
        ("chat_context_retention_messages", 0),
        ("anthropic_max_tokens", 0),
    ],
)
def test_invalid_config_raises_value_error(field: str, bad_value: object) -> None:
    with pytest.raises(ValueError, match=field.upper()):
        _make(**{field: bad_value})
