from __future__ import annotations

import logging

from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.auth import restricted
from app.core.config import get_config
from app.utils.i18n import locale_from_update, t

logger = logging.getLogger("serverwatch")


def build_main_keyboard(locale: str) -> ReplyKeyboardMarkup:
    """Return the persistent main keyboard."""
    return ReplyKeyboardMarkup(
        [
            [t("keyboard.status", locale=locale), t("keyboard.alerts", locale=locale)],
            [t("keyboard.models", locale=locale), t("keyboard.help", locale=locale)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


@restricted
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — detect locale, send personalised greeting, show keyboard."""
    user = update.effective_user
    name = user.first_name if user and user.first_name else "there"
    locale = locale_from_update(update, fallback=get_config().bot_locale)

    logger.info(
        "User %s started the bot (lang=%s, locale=%s)",
        user.id if user else "unknown",
        user.language_code if user else None,
        locale,
    )

    if update.effective_message is None:
        return

    await update.effective_message.reply_text(
        t("start.welcome", locale=locale, name=name),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_main_keyboard(locale),
    )
