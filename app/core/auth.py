"""Single-user authorization middleware.

Wraps any handler so only the configured TELEGRAM_CHAT_ID can
interact with the bot. Any other chat receives a friendly informational reply.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.config import get_config

logger = logging.getLogger("serverwatch")


def restricted(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that blocks messages from unauthorized chat IDs."""

    @wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        config = get_config()
        chat = update.effective_chat
        if chat is None or chat.id != config.telegram_chat_id:
            chat_id = chat.id if chat else "unknown"
            logger.warning("Unauthorized access attempt from chat_id=%s", chat_id)
            if update.effective_message:
                from app.utils.i18n import t  # local import to avoid circular deps
                from app.utils.i18n import locale_from_update

                await update.effective_message.reply_text(
                    t(
                        "errors.unauthorized",
                        locale=locale_from_update(update, fallback=config.bot_locale),
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            return
        return await func(update, context, *args, **kwargs)

    return wrapper
