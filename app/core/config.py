from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: int
    glances_base_url: str
    glances_request_timeout_seconds: float
    glances_log_full_payload: bool
    ollama_base_url: str
    ollama_model: str
    openai_api_key: str | None
    openai_model: str | None
    anthropic_api_key: str | None
    anthropic_model: str | None
    deepseek_api_key: str | None
    deepseek_model: str | None
    bot_log_level: str
    bot_locale: str
    sqlite_path: str
    alert_check_interval_seconds: int
    alert_cooldown_seconds: int
    alert_default_cpu_threshold: float
    alert_default_ram_threshold: float
    alert_default_disk_threshold: float
    chat_context_max_turns: int
    chat_context_max_chars: int
    chat_context_retention_messages: int
    tz: str

    @classmethod
    def from_env(cls) -> Config:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")

        raw_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not raw_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required")

        return cls(
            telegram_bot_token=token,
            telegram_chat_id=int(raw_chat_id),
            glances_base_url=os.getenv("GLANCES_BASE_URL", "http://glances:61208/api/4"),
            glances_request_timeout_seconds=float(
                os.getenv("GLANCES_REQUEST_TIMEOUT_SECONDS", "8.0")
            ),
            glances_log_full_payload=_env_bool("GLANCES_LOG_FULL_PAYLOAD", default=False),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
            openai_api_key=_optional_env("OPENAI_API_KEY"),
            openai_model=_optional_env("OPENAI_MODEL"),
            anthropic_api_key=_optional_env("ANTHROPIC_API_KEY"),
            anthropic_model=_optional_env("ANTHROPIC_MODEL"),
            deepseek_api_key=_optional_env("DEEPSEEK_API_KEY"),
            deepseek_model=_optional_env("DEEPSEEK_MODEL"),
            bot_log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
            bot_locale=os.getenv("BOT_LOCALE", "en"),
            sqlite_path=os.getenv("SQLITE_PATH", "/app/data/serverwatch.db"),
            alert_check_interval_seconds=int(os.getenv("ALERT_CHECK_INTERVAL_SECONDS", "60")),
            alert_cooldown_seconds=int(os.getenv("ALERT_COOLDOWN_SECONDS", "300")),
            alert_default_cpu_threshold=float(os.getenv("ALERT_DEFAULT_CPU_THRESHOLD", "85")),
            alert_default_ram_threshold=float(os.getenv("ALERT_DEFAULT_RAM_THRESHOLD", "85")),
            alert_default_disk_threshold=float(os.getenv("ALERT_DEFAULT_DISK_THRESHOLD", "90")),
            chat_context_max_turns=int(os.getenv("CHAT_CONTEXT_MAX_TURNS", "8")),
            chat_context_max_chars=int(os.getenv("CHAT_CONTEXT_MAX_CHARS", "6000")),
            chat_context_retention_messages=int(
                os.getenv("CHAT_CONTEXT_RETENTION_MESSAGES", "200")
            ),
            tz=os.getenv("TZ", "UTC"),
        )


_config: Config | None = None


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config
