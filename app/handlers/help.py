"""Handler for /help and ❓ Help button."""

from __future__ import annotations

import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.core.auth import restricted
from app.utils.i18n import t


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(t("help.text"), parse_mode=ParseMode.MARKDOWN)


@restricted
async def help_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(t("help.text"), parse_mode=ParseMode.MARKDOWN)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(f"^{re.escape(t('keyboard.help'))}$") & ~filters.COMMAND,
            help_button,
        )
    )
