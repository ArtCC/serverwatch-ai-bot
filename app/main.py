from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from app.core import store
from app.core.config import get_config
from app.handlers import alerts as alerts_handler
from app.handlers import chat as chat_handler
from app.handlers import help as help_handler
from app.handlers import models as models_handler
from app.handlers import status as status_handler
from app.handlers.start import start_handler
from app.utils.i18n import load as load_locale
from app.utils.i18n import t

logger = logging.getLogger("serverwatch")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler — logs the exception and replies with a friendly message."""
    logger.exception("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            t("errors.general"),
            parse_mode=ParseMode.MARKDOWN,
        )


async def post_init(application: Application) -> None:
    """Initialise DB and register bot commands after the Application is built."""
    await store.init_db()
    logger.info("Database initialised")

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Start the bot and show the keyboard"),
            BotCommand("status", "Current server metrics"),
            BotCommand("alerts", "View and configure alert thresholds"),
            BotCommand("models", "List and select Ollama models"),
            BotCommand("help", "Show help message"),
        ]
    )
    logger.info("Bot commands registered")


def main() -> None:
    config = get_config()

    logging.basicConfig(
        level=getattr(logging, config.bot_log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    load_locale(config.bot_locale)
    logger.info("ServerWatch AI Bot starting — locale=%s", config.bot_locale)

    app = Application.builder().token(config.telegram_bot_token).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_handler))
    status_handler.register(app)
    alerts_handler.register(app)
    models_handler.register(app)
    help_handler.register(app)
    # chat handler must be last — it catches all remaining text messages
    chat_handler.register(app)
    app.add_error_handler(error_handler)

    logger.info("Polling started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
