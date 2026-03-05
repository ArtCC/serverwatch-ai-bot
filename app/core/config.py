from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: int
    glances_base_url: str
    ollama_base_url: str
    ollama_model: str
    bot_log_level: str
    bot_locale: str
    sqlite_path: str
    alert_check_interval_seconds: int
    alert_cooldown_seconds: int
    alert_default_cpu_threshold: float
    alert_default_ram_threshold: float
    alert_default_disk_threshold: float
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
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
            bot_log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
            bot_locale=os.getenv("BOT_LOCALE", "en"),
            sqlite_path=os.getenv("SQLITE_PATH", "/app/data/serverwatch.db"),
            alert_check_interval_seconds=int(os.getenv("ALERT_CHECK_INTERVAL_SECONDS", "60")),
            alert_cooldown_seconds=int(os.getenv("ALERT_COOLDOWN_SECONDS", "300")),
            alert_default_cpu_threshold=float(os.getenv("ALERT_DEFAULT_CPU_THRESHOLD", "85")),
            alert_default_ram_threshold=float(os.getenv("ALERT_DEFAULT_RAM_THRESHOLD", "85")),
            alert_default_disk_threshold=float(os.getenv("ALERT_DEFAULT_DISK_THRESHOLD", "90")),
            tz=os.getenv("TZ", "UTC"),
        )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config
