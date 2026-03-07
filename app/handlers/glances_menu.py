"""Handler for /glances — on-demand, per-endpoint Glances details.

Flow:
  1. /glances or inline open button  -> show detail menu.
  2. User picks one endpoint          -> fetch that endpoint live now.
  3. Show endpoint payload + actions  -> Refresh / Back / Close.
"""

from __future__ import annotations

import json
import logging
from typing import cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.core import store
from app.core.auth import restricted
from app.core.config import get_config
from app.services import glances, llm_router
from app.utils.i18n import locale_from_update, t

logger = logging.getLogger("serverwatch")

_CB_OPEN = "glc_open"
_CB_SELECT_PREFIX = "glc_sel:"
_CB_REFRESH_PREFIX = "glc_ref:"
_CB_BACK = "glc_back"
_CB_CLOSE = "glc_close"

_MENU_KEYS: tuple[str, ...] = (
    "cpu",
    "mem",
    "fs",
    "load",
    "network",
    "containers",
    "processlist",
    "uptime",
    "system",
    "sensors",
)

_TELEGRAM_MAX_TEXT_LENGTH = 4096
_TELEGRAM_HARD_FALLBACK_LENGTH = 1000
_MAX_DETAIL_JSON_FOR_LLM = 12000

_DETAIL_SUMMARY_SYSTEM = """\
You are ServerWatch, a concise server monitoring assistant.
You are given live metrics from one Glances endpoint and must summarize them for Telegram.

Rules:
- Always reply in the user's language (locale: {locale}).
- Plain text only (no markdown, no code blocks, no JSON).
- Keep it short and practical (4-8 lines).
- Start with one status line: good / warning / critical.
- Mention only the most relevant facts from the provided data.
- End with one actionable recommendation or "No immediate action needed".
- Never invent metrics that are not present.
"""


def open_menu_callback_data() -> str:
    """Expose callback data so other handlers can open this menu inline."""
    return _CB_OPEN


def _label_for_key(key: str, locale: str) -> str:
    labels = {
        "cpu": t("glances.options.cpu", locale=locale),
        "mem": t("glances.options.mem", locale=locale),
        "fs": t("glances.options.fs", locale=locale),
        "load": t("glances.options.load", locale=locale),
        "network": t("glances.options.network", locale=locale),
        "containers": t("glances.options.containers", locale=locale),
        "processlist": t("glances.options.processlist", locale=locale),
        "uptime": t("glances.options.uptime", locale=locale),
        "system": t("glances.options.system", locale=locale),
        "sensors": t("glances.options.sensors", locale=locale),
    }
    return labels.get(key, key)


def _fit_for_telegram(text: str) -> str:
    if len(text) <= _TELEGRAM_MAX_TEXT_LENGTH:
        return text
    suffix = "\n\n...[truncated]"
    max_body = _TELEGRAM_MAX_TEXT_LENGTH - len(suffix)
    if max_body <= 0:
        return text[:_TELEGRAM_MAX_TEXT_LENGTH]
    return text[:max_body] + suffix


def _serialize_for_llm(payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=True)
    if len(serialized) <= _MAX_DETAIL_JSON_FOR_LLM:
        return serialized
    suffix = "...[truncated]"
    return serialized[: _MAX_DETAIL_JSON_FOR_LLM - len(suffix)] + suffix


def _fallback_summary(locale: str, label: str, payload: object) -> str:
    header = t("glances.detail_header", locale=locale, label=label)
    lines = [header]

    if isinstance(payload, dict):
        scalar_items: list[tuple[str, object]] = []
        for key, value in payload.items():
            if isinstance(value, (str, int, float, bool)):
                scalar_items.append((str(key), value))
        if scalar_items:
            lines.append("")
            lines.append("Summary")
            for key, value in scalar_items[:6]:
                lines.append(f"- {key}: {value}")
        else:
            lines.append("")
            lines.append("No compact scalar metrics available for this endpoint right now.")
    elif isinstance(payload, list):
        lines.append("")
        lines.append(f"Items returned: {len(payload)}")
        if payload and isinstance(payload[0], dict):
            first = payload[0]
            name = first.get("name") if isinstance(first, dict) else None
            if name:
                lines.append(f"First item: {name}")
    else:
        lines.append("")
        lines.append(f"Value: {payload}")

    lines.append("")
    lines.append("No immediate action needed")
    return "\n".join(lines)


async def _summarize_payload(*, locale: str, label: str, key: str, payload: object) -> str:
    selection = await store.get_active_model()
    system_prompt = _DETAIL_SUMMARY_SYSTEM.format(locale=locale)
    user_prompt = (
        f"Endpoint key: {key}\n"
        f"Endpoint label: {label}\n"
        "Live endpoint payload (JSON):\n"
        f"{_serialize_for_llm(payload)}"
    )

    try:
        summary = await llm_router.chat(selection, system_prompt, user_prompt)
        text = summary.strip()
        if not text:
            raise ValueError("empty summary")
        return text
    except Exception:
        logger.exception("Could not summarize Glances detail via LLM for key=%s", key)
        return _fallback_summary(locale, label, payload)


async def _safe_edit_detail(
    *, query: object, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    if not hasattr(query, "edit_message_text"):
        return

    candidate = _fit_for_telegram(text)
    try:
        await query.edit_message_text(candidate, reply_markup=reply_markup)
    except BadRequest as exc:
        lower = str(exc).lower()
        if "message is too long" not in lower and "message_too_long" not in lower:
            raise
        short = _fit_for_telegram(candidate[:_TELEGRAM_HARD_FALLBACK_LENGTH])
        await query.edit_message_text(short, reply_markup=reply_markup)


def _menu_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _label_for_key("cpu", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}cpu",
                ),
                InlineKeyboardButton(
                    _label_for_key("mem", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}mem",
                ),
                InlineKeyboardButton(
                    _label_for_key("fs", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}fs",
                ),
            ],
            [
                InlineKeyboardButton(
                    _label_for_key("load", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}load",
                ),
                InlineKeyboardButton(
                    _label_for_key("network", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}network",
                ),
                InlineKeyboardButton(
                    _label_for_key("containers", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}containers",
                ),
            ],
            [
                InlineKeyboardButton(
                    _label_for_key("processlist", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}processlist",
                ),
                InlineKeyboardButton(
                    _label_for_key("uptime", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}uptime",
                ),
            ],
            [
                InlineKeyboardButton(
                    _label_for_key("system", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}system",
                ),
                InlineKeyboardButton(
                    _label_for_key("sensors", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}sensors",
                ),
            ],
            [
                InlineKeyboardButton(
                    t("glances.close_button", locale=locale),
                    callback_data=_CB_CLOSE,
                )
            ],
        ]
    )


def _detail_keyboard(locale: str, key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("glances.refresh_button", locale=locale),
                    callback_data=f"{_CB_REFRESH_PREFIX}{key}",
                ),
                InlineKeyboardButton(
                    t("glances.back_button", locale=locale),
                    callback_data=_CB_BACK,
                ),
            ],
            [
                InlineKeyboardButton(
                    t("glances.close_button", locale=locale),
                    callback_data=_CB_CLOSE,
                )
            ],
        ]
    )


async def _render_menu(update: Update, *, edit: bool) -> None:
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    text = f"{t('glances.header', locale=locale)}\n\n{t('glances.menu_hint', locale=locale)}"
    keyboard = _menu_keyboard(locale)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        return

    if update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


async def _render_detail(update: Update, *, key: str) -> None:
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    label = _label_for_key(key, locale)

    payload: object | None = None
    try:
        payload = await glances.get_live_endpoint_detail(key)
        summary = await _summarize_payload(locale=locale, label=label, key=key, payload=payload)
        text = f"{t('glances.detail_header', locale=locale, label=label)}\n\n{summary}"
    except Exception:
        if payload is None:
            logger.exception("Could not fetch live Glances detail for key=%s", key)
            text = t("glances.unavailable", locale=locale, label=label)
        else:
            text = _fallback_summary(locale, label, payload)

    keyboard = _detail_keyboard(locale, key)
    query = update.callback_query
    if query is None:
        if update.effective_message:
            await update.effective_message.reply_text(
                _fit_for_telegram(text),
                reply_markup=keyboard,
            )
        return
    await _safe_edit_detail(query=query, text=text, reply_markup=keyboard)


@restricted
async def glances_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render_menu(update, edit=False)


@restricted
async def cb_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await _render_menu(update, edit=True)


@restricted
async def cb_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    key = (query.data or "").replace(_CB_SELECT_PREFIX, "", 1)
    if key not in _MENU_KEYS:
        return

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render_detail(update, key=key)


@restricted
async def cb_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    key = (query.data or "").replace(_CB_REFRESH_PREFIX, "", 1)
    if key not in _MENU_KEYS:
        return

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render_detail(update, key=key)


@restricted
async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await _render_menu(update, edit=True)


@restricted
async def cb_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if query.message is None:
        return

    try:
        await cast(Message, query.message).delete()
    except Exception:
        logger.warning("Could not delete Glances menu message on close", exc_info=True)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("glances", glances_command))
    app.add_handler(CallbackQueryHandler(cb_open, pattern=f"^{_CB_OPEN}$"))
    app.add_handler(CallbackQueryHandler(cb_select, pattern=rf"^{_CB_SELECT_PREFIX}.+$"))
    app.add_handler(CallbackQueryHandler(cb_refresh, pattern=rf"^{_CB_REFRESH_PREFIX}.+$"))
    app.add_handler(CallbackQueryHandler(cb_back, pattern=f"^{_CB_BACK}$"))
    app.add_handler(CallbackQueryHandler(cb_close, pattern=f"^{_CB_CLOSE}$"))
