from app.utils.i18n import resolve_locale, text_matches_key


def test_resolve_locale_prefers_specific_or_base_language() -> None:
    assert resolve_locale("es-ES", fallback="en") == "es"
    assert resolve_locale("en-US", fallback="es") == "en"


def test_resolve_locale_falls_back_to_configured_locale() -> None:
    assert resolve_locale("pt-BR", fallback="es") == "es"


def test_text_matches_key_across_supported_locales() -> None:
    assert text_matches_key("📊 Status", "keyboard.status")
    assert text_matches_key("📊 Estado", "keyboard.status")
    assert text_matches_key("📊 État", "keyboard.status")
    assert text_matches_key("❓ Hilfe", "keyboard.help")
    assert not text_matches_key("random text", "keyboard.status")
