from __future__ import annotations

import logging

from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.auth import restricted
from app.utils.i18n import t

logger = logging.getLogger("serverwatch")


def build_main_keyboard() -> ReplyKeyboardMarkup:
    """Return the persistent main keyboard."""
    return ReplyKeyboardMarkup(
        [
            [t("keyboard.status"), t("keyboard.alerts")],
            [t("keyboard.models"), t("keyboard.help")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


@restricted
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — send personalised greeting and show persistent keyboard."""
    user = update.effective_user
    name = user.first_name if user and user.first_name else "there"

    await update.message.reply_text(  # type: ignore[union-attr]
        t("start.welcome", name=name),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_main_keyboard(),
    )
    logger.info("User %s started the bot", user.id if user else "unknown")
