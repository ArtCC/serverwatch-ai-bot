"""Handler for /author."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from app.core.auth import restricted
from app.core.config import get_config
from app.utils.i18n import locale_from_update, t


@restricted
async def author_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    if update.effective_message:
        await update.effective_message.reply_text(
            t("author.text", locale=locale),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("author", author_command))
