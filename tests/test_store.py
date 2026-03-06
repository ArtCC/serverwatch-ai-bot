from app.core.store import normalize_model_selection, split_model_selection


def test_normalize_model_selection_supports_legacy_ollama_names() -> None:
    assert normalize_model_selection("llama3.2:3b") == "ollama:llama3.2:3b"


def test_normalize_model_selection_keeps_supported_provider_prefixes() -> None:
    assert normalize_model_selection("openai:gpt-4o-mini") == "openai:gpt-4o-mini"


def test_split_model_selection() -> None:
    assert split_model_selection("anthropic:claude-3-5-sonnet-latest") == (
        "anthropic",
        "claude-3-5-sonnet-latest",
    )
