"""Handler for free-text messages — gather live metrics and query the LLM.

Flow:
  1. User sends any text that is not a command or a keyboard button.
  2. Bot shows "typing…" and sends a ⏳ placeholder.
  3. Fetch Glances snapshot (best-effort — partial failure is tolerated).
  4. Build a system prompt with the metrics context.
  5. Send user message + context to Ollama using the active model.
  6. Edit the placeholder with the LLM response.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.core import store
from app.core.auth import restricted
from app.services import glances, ollama
from app.utils.i18n import t

logger = logging.getLogger("serverwatch")

_SYSTEM_WITH_METRICS = """\
You are ServerWatch, a concise and helpful server monitoring assistant.
The user is querying you from Telegram about their server.
Respond in plain text — no markdown, no code blocks unless explicitly asked.
Keep answers short and actionable.

Current server metrics:
{metrics}
"""

_SYSTEM_NO_METRICS = """\
You are ServerWatch, a concise and helpful server monitoring assistant.
The user is querying you from Telegram about their server.
Respond in plain text — no markdown, no code blocks unless explicitly asked.
Keep answers short and actionable.
Note: live server metrics are currently unavailable.
"""

# Keyboard button texts that must NOT be caught by this handler
_BUTTON_TEXTS = {"📊 Status", "🔔 Alerts", "🤖 Models", "❓ Help"}


@restricted
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return

    if message.text in _BUTTON_TEXTS:
        return

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)

    placeholder = await message.reply_text(t("chat.thinking"), parse_mode=ParseMode.MARKDOWN)

    # Fetch metrics (non-blocking failure)
    system_prompt: str
    try:
        snapshot = await glances.get_snapshot()
        system_prompt = _SYSTEM_WITH_METRICS.format(metrics=snapshot.as_text())
    except Exception:
        logger.warning("Could not fetch Glances snapshot for chat context")
        system_prompt = _SYSTEM_NO_METRICS

    # Get active model
    model = await store.get_active_model()

    # Query LLM
    try:
        reply = await ollama.chat(model, system_prompt, message.text)
    except Exception:
        logger.exception("Ollama chat request failed")
        await placeholder.edit_text(t("chat.error"), parse_mode=ParseMode.MARKDOWN)
        return

    # Telegram messages can be 4096 chars max
    if len(reply) > 4096:
        reply = reply[:4090] + "…"

    await placeholder.edit_text(reply, parse_mode=ParseMode.MARKDOWN)


def register(app: Application) -> None:
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            chat_handler,
        )
    )
