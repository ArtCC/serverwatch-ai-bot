from app.handlers.models import _models_keyboard
from app.services.ollama import _extract_model_names


def test_models_keyboard_uses_short_tokens_for_callbacks() -> None:
    long_name = "very-long-model-name-" + ("x" * 120)
    keyboard, mapping = _models_keyboard(["active", long_name], active="active")

    assert mapping == {"0": long_name}
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
