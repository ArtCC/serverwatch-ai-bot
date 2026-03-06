from app.core.config import Config
from app.handlers.models import _models_keyboard
from app.services.llm_router import ModelOption, configured_cloud_options
from app.services.ollama import _extract_model_names


def test_models_keyboard_uses_short_tokens_for_callbacks() -> None:
    long_name = "very-long-model-name-" + ("x" * 120)
    options = [
        ModelOption(selection="ollama:active", provider="ollama", model="active"),
        ModelOption(selection=f"ollama:{long_name}", provider="ollama", model=long_name),
    ]
    keyboard, mapping = _models_keyboard(options, active="ollama:active", locale="en")

    assert mapping == {"0": f"ollama:{long_name}"}
    callback = keyboard.inline_keyboard[0][0].callback_data
    assert callback is not None
    assert callback.startswith("mdl_sel:")
    assert len(callback.encode("utf-8")) <= 64


def test_extract_model_names_ignores_invalid_entries() -> None:
    data = {
        "models": [
            {"name": "llama3.2:3b"},
            {"name": ""},
            {"id": "no-name"},
            "invalid",
            {"name": "mistral:7b"},
        ]
    }

    assert _extract_model_names(data) == ["llama3.2:3b", "mistral:7b"]


def test_configured_cloud_options_only_returns_fully_configured_providers() -> None:
    cfg = Config(
        telegram_bot_token="token",
        telegram_chat_id=1,
        glances_base_url="http://glances:61208/api/4",
        ollama_base_url="http://host.docker.internal:11434",
        ollama_model="llama3.2:3b",
        openai_api_key="openai-key",
        openai_model="gpt-4o-mini",
        anthropic_api_key=None,
        anthropic_model="claude-3-5-sonnet-latest",
        deepseek_api_key="deepseek-key",
        deepseek_model="deepseek-chat",
        bot_log_level="INFO",
        bot_locale="en",
        sqlite_path="/tmp/serverwatch.db",
        alert_check_interval_seconds=60,
        alert_cooldown_seconds=300,
        alert_default_cpu_threshold=85.0,
        alert_default_ram_threshold=85.0,
        alert_default_disk_threshold=90.0,
        tz="UTC",
    )

    options = configured_cloud_options(cfg)

    assert options == [
        ModelOption(selection="openai:gpt-4o-mini", provider="openai", model="gpt-4o-mini"),
        ModelOption(selection="deepseek:deepseek-chat", provider="deepseek", model="deepseek-chat"),
    ]
