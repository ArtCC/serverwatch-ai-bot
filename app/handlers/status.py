"""Handler for /status — server metrics snapshot.

Flow:
  1. /status or 📊 Status button  → fetch Glances snapshot → render metrics card.
  2. 🔄 Refresh inline button     → edit the message with a fresh snapshot.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.auth import restricted
from app.core.config import get_config
from app.handlers.glances_menu import open_menu_callback_data
from app.services import glances
from app.utils.i18n import locale_from_update, regex_for_key, t, text_matches_key

logger = logging.getLogger("serverwatch")

_CB_REFRESH = "status_refresh"


def _refresh_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("status.refresh_button", locale=locale),
                    callback_data=_CB_REFRESH,
                )
            ],
            [
                InlineKeyboardButton(
                    t("status.glances_button", locale=locale),
                    callback_data=open_menu_callback_data(),
                )
            ],
        ]
    )


async def _render(update: Update, edit: bool = False) -> None:
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    try:
        snapshot = await glances.get_snapshot()
        text = f"{t('status.header', locale=locale)}\n\n{snapshot.as_text(locale=locale)}"
    except Exception:
        logger.exception("Failed to fetch Glances snapshot")
        text = t("status.unavailable", locale=locale)

    keyboard = _refresh_keyboard(locale)

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        except BadRequest as exc:
            # Common when refresh is pressed and metrics text has not changed.
            if "message is not modified" in str(exc).lower():
                return
            logger.warning("Could not edit status message on refresh: %s", exc)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render(update)


@restricted
async def status_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    if not text_matches_key(message.text, "keyboard.status"):
        return

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render(update)


@restricted
async def cb_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)

    await _render(update, edit=True)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(regex_for_key("keyboard.status")) & ~filters.COMMAND,
            status_button,
        )
    )
    app.add_handler(CallbackQueryHandler(cb_refresh, pattern=f"^{_CB_REFRESH}$"))
