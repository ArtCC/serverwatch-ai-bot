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
import time

from telegram import Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from app.core import store
from app.core.auth import restricted
from app.core.config import get_config
from app.services import glances, llm_router
from app.utils.i18n import locale_from_update, t, text_matches_key

logger = logging.getLogger("serverwatch")

_STREAM_EDIT_INTERVAL_SECONDS = 0.8
_STREAM_TYPING_INTERVAL_SECONDS = 4.0
_TELEGRAM_MAX_TEXT_LENGTH = 4096

_SYSTEM_WITH_METRICS = """\
You are ServerWatch, a concise and helpful server monitoring assistant.
The user is querying you from Telegram about their server.
Respond in plain text — no markdown, no code blocks unless explicitly asked.
Keep answers short and actionable.
Always reply in the user's language (locale: {locale}).

Current server metrics (aggregated Glances JSON payload):
{metrics_json}
"""

_SYSTEM_NO_METRICS = """\
You are ServerWatch, a concise and helpful server monitoring assistant.
The user is querying you from Telegram about their server.
Respond in plain text — no markdown, no code blocks unless explicitly asked.
Keep answers short and actionable.
Always reply in the user's language (locale: {locale}).
"""

_SYSTEM_METRICS_UNAVAILABLE = """\
You are ServerWatch, a concise and helpful server monitoring assistant.
The user is querying you from Telegram about their server.
Respond in plain text — no markdown, no code blocks unless explicitly asked.
Keep answers short and actionable.
Always reply in the user's language (locale: {locale}).
Live server metrics were requested for this answer, but they are currently unavailable.
If needed, briefly mention that live metrics are unavailable right now,
then continue with best-effort guidance.
"""

_TOOL_DECIDER_SYSTEM = """\
You are a routing assistant for a server monitoring bot.
Decide if you need live Glances metrics to answer the user's message well.

Rules:
- Return exactly `USE_GLANCES` if live server metrics are needed.
- Return exactly `NO_GLANCES` if they are not needed.
- Do not add any other text.

Use `USE_GLANCES` for requests about status, health, CPU, RAM, disk, network,
containers, processes, temperature, uptime, bottlenecks, troubleshooting or
performance diagnosis.
Use `NO_GLANCES` for generic chat, explanations, writing, or topics unrelated
to the current server state.
"""

# Keyboard button locale keys — resolved at runtime so any locale is covered
_BUTTON_KEYS = (
    "keyboard.status",
    "keyboard.alerts",
    "keyboard.models",
    "keyboard.help",
)

_GLANCES_HINTS = (
    "status",
    "estado",
    "nas",
    "server",
    "cpu",
    "ram",
    "mem",
    "memoria",
    "swap",
    "disk",
    "disco",
    "storage",
    "load",
    "network",
    "red",
    "latencia",
    "latency",
    "proceso",
    "process",
    "docker",
    "container",
    "uptime",
    "temperatura",
    "temperature",
    "bottleneck",
    "rendimiento",
    "performance",
)


def _is_keyboard_button_text(text: str) -> bool:
    return any(text_matches_key(text, key) for key in _BUTTON_KEYS)


def _provider_display_name(provider: str) -> str:
    return {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "deepseek": "DeepSeek",
        "ollama": "Ollama",
    }.get(provider, provider)


def _decider_wants_glances(raw: str) -> bool:
    normalized = raw.strip().upper()
    return normalized == "USE_GLANCES"


async def _llm_should_use_glances(selection: str, user_message: str) -> bool:
    try:
        decision = await llm_router.chat(selection, _TOOL_DECIDER_SYSTEM, user_message)
    except Exception:
        logger.warning("Tool decider failed; defaulting to NO_GLANCES")
        return False
    return _decider_wants_glances(decision)


def _quick_glances_decision(user_message: str) -> bool | None:
    """Fast local heuristic to avoid the routing LLM call when obvious."""
    text = user_message.casefold()
    if any(token in text for token in _GLANCES_HINTS):
        return True
    if len(text) <= 12:
        return False
    return None


def _truncate_for_telegram(text: str) -> str:
    if len(text) <= _TELEGRAM_MAX_TEXT_LENGTH:
        return text
    return text[: _TELEGRAM_MAX_TEXT_LENGTH - 1] + "…"


async def _safe_edit_or_reply(
    *,
    source_message: Message,
    placeholder: Message | None,
    text: str,
    parse_mode: str | None = None,
    allow_fallback_reply: bool = True,
) -> bool:
    """Try editing placeholder first; fallback to a new reply on Telegram failures."""
    if placeholder is not None:
        try:
            await placeholder.edit_text(text, parse_mode=parse_mode)
            return True
        except BadRequest as exc:
            # Harmless in case of duplicate updates/races.
            if "message is not modified" in str(exc).lower():
                return True
            logger.warning("Could not edit placeholder message: %s", exc)
        except (TimedOut, NetworkError):
            logger.warning("Telegram timeout/network error while editing placeholder")
        except Exception:
            logger.exception("Unexpected error while editing placeholder")

    if not allow_fallback_reply:
        return False

    try:
        await source_message.reply_text(text, parse_mode=parse_mode)
        return True
    except Exception:
        logger.exception("Fallback reply_text failed")
        return False


@restricted
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return

    if _is_keyboard_button_text(message.text):
        return

    locale = locale_from_update(update, fallback=get_config().bot_locale)

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)

    placeholder: Message | None = None
    try:
        placeholder = await message.reply_text(
            t("chat.thinking", locale=locale),
            parse_mode=ParseMode.MARKDOWN,
        )
    except (TimedOut, NetworkError):
        logger.warning("Telegram timeout/network error while sending thinking placeholder")
    except Exception:
        logger.exception("Unexpected error while sending thinking placeholder")

    # Fetch metrics (non-blocking failure)
    system_prompt: str
    selection = await store.get_active_model()
    provider, _, _ = selection.partition(":")

    quick_decision = _quick_glances_decision(message.text)
    if quick_decision is None:
        if provider in {"openai", "anthropic", "deepseek"}:
            # Cloud path optimization: skip routing LLM call when intent is ambiguous.
            use_glances = False
        else:
            use_glances = await _llm_should_use_glances(selection, message.text)
    else:
        use_glances = quick_decision
    try:
        if use_glances:
            snapshot = await glances.get_snapshot()
            system_prompt = _SYSTEM_WITH_METRICS.format(
                locale=locale,
                metrics_json=snapshot.as_raw_json(),
            )
        else:
            system_prompt = _SYSTEM_NO_METRICS.format(locale=locale)
    except Exception:
        logger.warning("Could not fetch Glances snapshot for chat context")
        if use_glances:
            system_prompt = _SYSTEM_METRICS_UNAVAILABLE.format(locale=locale)
        else:
            system_prompt = _SYSTEM_NO_METRICS.format(locale=locale)

    # Query LLM (streaming when supported by provider)
    reply_accumulated = ""
    last_pushed = ""
    last_edit_at = 0.0
    last_typing_at = time.monotonic()
    stream_push_enabled = True

    try:
        async for chunk in llm_router.stream_chat(selection, system_prompt, message.text):
            if not chunk:
                continue

            reply_accumulated += chunk

            # Without a placeholder we avoid sending many partial replies.
            if placeholder is None:
                continue

            now = time.monotonic()
            candidate = _truncate_for_telegram(reply_accumulated)

            if update.effective_chat and (now - last_typing_at) >= _STREAM_TYPING_INTERVAL_SECONDS:
                await update.effective_chat.send_action(ChatAction.TYPING)
                last_typing_at = now

            if (
                stream_push_enabled
                and candidate != last_pushed
                and (now - last_edit_at) >= _STREAM_EDIT_INTERVAL_SECONDS
            ):
                pushed = await _safe_edit_or_reply(
                    source_message=message,
                    placeholder=placeholder,
                    text=candidate,
                    allow_fallback_reply=False,
                )
                if pushed:
                    last_pushed = candidate
                    last_edit_at = now
                else:
                    # Avoid repeated partial fallback replies if placeholder edits keep failing.
                    stream_push_enabled = False
    except Exception:
        logger.exception("LLM chat request failed for provider=%s", provider)
        await _safe_edit_or_reply(
            source_message=message,
            placeholder=placeholder,
            text=t("chat.provider_error", locale=locale, provider=_provider_display_name(provider)),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not reply_accumulated.strip():
        reply_accumulated = t("chat.error", locale=locale)

    final_reply = _truncate_for_telegram(reply_accumulated)

    if final_reply != last_pushed or placeholder is None:
        await _safe_edit_or_reply(source_message=message, placeholder=placeholder, text=final_reply)


def register(app: Application) -> None:
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            chat_handler,
        )
    )
