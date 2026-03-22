from __future__ import annotations

import asyncio
import logging

import httpx
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes

from app.core import store
from app.core.config import get_config
from app.handlers import alerts as alerts_handler
from app.handlers import author as author_handler
from app.handlers import chat as chat_handler
from app.handlers import glances_menu as glances_menu_handler
from app.handlers import help as help_handler
from app.handlers import models as models_handler
from app.handlers import status as status_handler
from app.handlers.start import start_handler
from app.services import glances as glances_service
from app.services import llm_router as llm_router_service
from app.services import ollama as ollama_service
from app.services import scheduler as scheduler_service
from app.utils.i18n import load as load_locale
from app.utils.i18n import locale_from_update, supported_locales, t

logger = logging.getLogger("serverwatch")


def _commands_for_locale(locale: str) -> list[BotCommand]:
    """Build the bot command list with localised descriptions."""
    return [
        BotCommand("start", t("commands.start", locale=locale)),
        BotCommand("status", t("commands.status", locale=locale)),
        BotCommand("alerts", t("commands.alerts", locale=locale)),
        BotCommand("glances", t("commands.glances", locale=locale)),
        BotCommand("models", t("commands.models", locale=locale)),
        BotCommand("context", t("commands.context", locale=locale)),
        BotCommand("author", t("commands.author", locale=locale)),
        BotCommand("help", t("commands.help", locale=locale)),
    ]


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler — logs the exception and replies with a friendly message."""
    err = context.error
    logger.exception("Unhandled exception", exc_info=err)

    # Handle RetryAfter (flood control) by waiting and notifying the user
    if isinstance(err, RetryAfter):
        _delay = (
            err.retry_after if isinstance(err.retry_after, int) else err.retry_after.total_seconds()
        )
        logger.warning("Flood control exceeded. Waiting %d seconds before retry.", _delay)
        await asyncio.sleep(_delay)
        # After waiting, don't send a message to avoid triggering another flood
        return

    if isinstance(update, Update) and update.effective_message:
        locale = locale_from_update(update, fallback=get_config().bot_locale)
        key = "errors.general"
        if isinstance(err, BadRequest):
            key = "errors.telegram_bad_request"
        elif isinstance(err, (TimedOut, NetworkError, httpx.TimeoutException, httpx.HTTPError)):
            key = "errors.service_unavailable"

        try:
            await update.effective_message.reply_text(
                t(key, locale=locale),
                parse_mode=ParseMode.MARKDOWN,
            )
        except RetryAfter as retry_err:
            # If we get flood control when trying to report an error, just wait silently
            logger.warning(
                "Flood control during error reporting. Waiting %d seconds.",
                retry_err.retry_after,
            )
            _err_delay = (
                retry_err.retry_after
                if isinstance(retry_err.retry_after, int)
                else retry_err.retry_after.total_seconds()
            )
            await asyncio.sleep(_err_delay)


async def post_init(application: Application) -> None:
    """Initialise DB and register bot commands after the Application is built."""
    await store.init_db()
    logger.info("Database initialised")

    default_locale = get_config().bot_locale
    await application.bot.set_my_commands(_commands_for_locale(default_locale))

    for locale in supported_locales():
        await application.bot.set_my_commands(_commands_for_locale(locale), language_code=locale)

    logger.info("Bot commands registered")


async def post_shutdown(application: Application) -> None:
    """Release shared service resources created during runtime."""
    await store.close_db()
    await glances_service.close_client()
    await llm_router_service.close_client()
    await ollama_service.close_clients()
    logger.info("Runtime resources closed")


def main() -> None:
    config = get_config()

    logging.basicConfig(
        level=getattr(logging, config.bot_log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    load_locale(config.bot_locale)
    logger.info("ServerWatch AI Bot starting — locale=%s", config.bot_locale)

    app = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_handler))
    status_handler.register(app)
    alerts_handler.register(app)
    glances_menu_handler.register(app)
    models_handler.register(app)
    author_handler.register(app)
    help_handler.register(app)
    # chat handler must be last — it catches all remaining text messages
    chat_handler.register(app)
    app.add_error_handler(error_handler)
    scheduler_service.register(app)

    logger.info("Polling started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
