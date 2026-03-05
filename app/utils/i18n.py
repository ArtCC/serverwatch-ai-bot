"""Lightweight i18n helper.

Usage:
    from app.utils.i18n import t

    t("start.welcome")
    t("alerts.threshold_line", metric="CPU", value=85)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_strings: dict[str, Any] = {}
_loaded_locale: str = ""

_LOCALE_DIR = Path(__file__).parent.parent.parent / "locale"


def load(locale: str = "en") -> None:
    """Load the given locale file into memory. Call once at startup."""
    global _strings, _loaded_locale
    path = _LOCALE_DIR / f"{locale}.json"
    if not path.exists():
        raise FileNotFoundError(f"Locale file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        _strings = json.load(fh)
    _loaded_locale = locale


def detect_and_load(telegram_lang: str | None, fallback: str) -> None:
    """Detect the best locale from a Telegram language_code and load it.

    Telegram sends codes like "es", "en", "es-ES", "pt-BR". We try the
    full code first, then the base language, then *fallback*.
    If the same locale is already loaded, nothing happens.
    """
    candidates: list[str] = []
    if telegram_lang:
        candidates.append(telegram_lang.lower().replace("-", "_"))
        base = telegram_lang.split("-")[0].lower()
        if base not in candidates:
            candidates.append(base)
    candidates.append(fallback)

    for candidate in candidates:
        if candidate == _loaded_locale:
            return
        if (_LOCALE_DIR / f"{candidate}.json").exists():
            load(candidate)
            return


def get_locale() -> str:
    """Return the currently loaded locale code (e.g. 'es', 'en')."""
    return _loaded_locale or "en"


def t(key: str, **kwargs: Any) -> str:
    """Return the localised string for *key* (dot-separated path).

    Optional keyword arguments are interpolated via str.format().
    Raises KeyError if the key does not exist.
    """
    if not _strings:
        load()

    parts = key.split(".")
    value: Any = _strings
    for part in parts:
        if not isinstance(value, dict):
            raise KeyError(f"i18n key not found: '{key}'")
        value = value[part]

    if not isinstance(value, str):
        raise TypeError(f"i18n key '{key}' resolved to {type(value).__name__}, expected str")

    return value.format(**kwargs) if kwargs else value
