"""Lightweight i18n helper.

Usage:
    from app.utils.i18n import t

    t("start.welcome")
    t("alerts.threshold_line", metric="CPU", value=85)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

_strings: dict[str, Any] = {}
_loaded_locale: str = ""
_cache: dict[str, dict[str, Any]] = {}
_supported_locales_cache: tuple[str, ...] | None = None
_translations_cache: dict[str, frozenset[str]] = {}
_regex_cache: dict[str, str] = {}

if TYPE_CHECKING:
    from telegram import Update

_LOCALE_DIR = Path(__file__).parent.parent.parent / "locale"


def _load_locale_file(locale: str) -> dict[str, Any]:
    if locale in _cache:
        return _cache[locale]

    path = _LOCALE_DIR / f"{locale}.json"
    if not path.exists():
        raise FileNotFoundError(f"Locale file not found: {path}")

    with path.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):
        raise TypeError(f"Locale file {path} must contain an object at root")

    _cache[locale] = loaded
    return loaded


def supported_locales() -> list[str]:
    """Return all available locale codes from /locale."""
    global _supported_locales_cache
    if _supported_locales_cache is None:
        _supported_locales_cache = tuple(sorted(path.stem for path in _LOCALE_DIR.glob("*.json")))
    return list(_supported_locales_cache)


def load(locale: str = "en") -> None:
    """Load the given locale file into memory. Call once at startup."""
    global _strings, _loaded_locale
    _strings = _load_locale_file(locale)
    _loaded_locale = locale


def resolve_locale(telegram_lang: str | None, fallback: str) -> str:
    """Resolve the best locale code from Telegram language + fallback."""
    candidates: list[str] = []
    available = set(supported_locales())

    if telegram_lang:
        full = telegram_lang.lower().replace("-", "_")
        candidates.append(full)
        base = full.split("_")[0]
        if base not in candidates:
            candidates.append(base)

    fallback_norm = fallback.lower().replace("-", "_")
    candidates.append(fallback_norm)
    if "en" not in candidates:
        candidates.append("en")

    for candidate in candidates:
        if candidate in available:
            return candidate
    return "en"


def detect_and_load(telegram_lang: str | None, fallback: str) -> None:
    """Detect the best locale from a Telegram language_code and load it.

    Telegram sends codes like "es", "en", "es-ES", "pt-BR". We try the
    full code first, then the base language, then *fallback*.
    If the same locale is already loaded, nothing happens.
    """
    resolved = resolve_locale(telegram_lang, fallback)
    if resolved != _loaded_locale:
        load(resolved)


def get_locale() -> str:
    """Return the currently loaded locale code (e.g. 'es', 'en')."""
    return _loaded_locale or "en"


def locale_from_update(update: Update | None, fallback: str) -> str:
    """Resolve locale for an incoming Telegram update."""
    telegram_lang = None
    if update and update.effective_user:
        telegram_lang = update.effective_user.language_code
    return resolve_locale(telegram_lang, fallback)


def _lookup(strings_obj: dict[str, Any], key: str) -> str:
    parts = key.split(".")
    value: Any = strings_obj
    for part in parts:
        if not isinstance(value, dict):
            raise KeyError(f"i18n key not found: '{key}'")
        value = value[part]

    if not isinstance(value, str):
        raise TypeError(f"i18n key '{key}' resolved to {type(value).__name__}, expected str")
    return value


def all_translations(key: str) -> set[str]:
    """Return all translated strings for the given key across locales."""
    cached = _translations_cache.get(key)
    if cached is not None:
        return set(cached)

    values: set[str] = set()
    for locale in supported_locales():
        strings_obj = _load_locale_file(locale)
        try:
            values.add(_lookup(strings_obj, key))
        except (KeyError, TypeError):
            continue
    _translations_cache[key] = frozenset(values)
    return values


def text_matches_key(text: str, key: str) -> bool:
    """Check whether text equals the translation of key in any locale."""
    cached = _translations_cache.get(key)
    if cached is None:
        cached = frozenset(all_translations(key))
        _translations_cache[key] = cached
    return text in cached


def regex_for_key(key: str) -> str:
    """Return an anchored regex that matches all locale variants for a key."""
    cached = _regex_cache.get(key)
    if cached is not None:
        return cached

    values = sorted(all_translations(key))
    if not values:
        _regex_cache[key] = r"^$"
        return _regex_cache[key]
    escaped = "|".join(re.escape(v) for v in values)
    _regex_cache[key] = rf"^({escaped})$"
    return _regex_cache[key]


def t(key: str, locale: str | None = None, **kwargs: Any) -> str:
    """Return the localised string for *key* (dot-separated path).

    Optional keyword arguments are interpolated via str.format().
    Raises KeyError if the key does not exist.
    """
    if locale is None:
        if not _strings:
            load()
        value = _lookup(_strings, key)
    else:
        value = _lookup(_load_locale_file(locale), key)

    return value.format(**kwargs) if kwargs else value
