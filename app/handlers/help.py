"""Handler for /help and ❓ Help button."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.core.auth import restricted
from app.core.config import get_config
from app.utils.i18n import locale_from_update, t, text_matches_key


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    if update.effective_message:
        await update.effective_message.reply_text(
            t("help.text", locale=locale),
            parse_mode=ParseMode.MARKDOWN,
        )


@restricted
async def help_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    if not text_matches_key(message.text, "keyboard.help"):
        return

    locale = locale_from_update(update, fallback=get_config().bot_locale)
    if update.effective_message:
        await update.effective_message.reply_text(
            t("help.text", locale=locale),
            parse_mode=ParseMode.MARKDOWN,
        )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            help_button,
        )
    )
