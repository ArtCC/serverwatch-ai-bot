"""Handler for /status — server metrics snapshot.

Flow:
  1. /status or 📊 Status button  → fetch Glances snapshot → render metrics card.
  2. 🔄 Refresh inline button     → edit the message with a fresh snapshot.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core.auth import restricted
from app.services import glances
from app.utils.i18n import t

logger = logging.getLogger("serverwatch")

_CB_REFRESH = "status_refresh"


def _refresh_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("status.refresh_button"), callback_data=_CB_REFRESH)]]
    )


async def _render(update: Update, edit: bool = False) -> None:
    try:
        snapshot = await glances.get_snapshot()
        text = f"{t('status.header')}\n\n{snapshot.as_text()}"
    except Exception:
        logger.exception("Failed to fetch Glances snapshot")
        text = t("status.unavailable")

    keyboard = _refresh_keyboard()

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )


@restricted
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render(update)


@restricted
async def status_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            filters.TEXT & filters.Regex(r"^📊 Status$") & ~filters.COMMAND,
            status_button,
        )
    )
    app.add_handler(CallbackQueryHandler(cb_refresh, pattern=f"^{_CB_REFRESH}$"))
