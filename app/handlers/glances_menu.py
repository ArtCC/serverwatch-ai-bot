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
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.core.auth import restricted
from app.core.config import get_config
from app.services import glances
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


def _truncate_for_telegram(text: str) -> str:
    if len(text) <= _TELEGRAM_MAX_TEXT_LENGTH:
        return text
    return text[: _TELEGRAM_MAX_TEXT_LENGTH - 25] + "\n\n...[truncated]"


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

    try:
        payload = await glances.get_live_endpoint_detail(key)
        payload_json = json.dumps(payload, ensure_ascii=True, indent=2)
        body = _truncate_for_telegram(payload_json)
        text = f"{t('glances.detail_header', locale=locale, label=label)}\n\n{body}"
    except Exception:
        logger.exception("Could not fetch live Glances detail for key=%s", key)
        text = t("glances.unavailable", locale=locale, label=label)

    keyboard = _detail_keyboard(locale, key)
    query = update.callback_query
    if query is None:
        if update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=keyboard)
        return
    await query.edit_message_text(text, reply_markup=keyboard)


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
