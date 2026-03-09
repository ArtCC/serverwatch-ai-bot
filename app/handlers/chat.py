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
from typing import cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.core import store
from app.core.auth import restricted
from app.core.config import get_config
from app.services import glances, llm_router
from app.utils.i18n import locale_from_update, t, text_matches_key

logger = logging.getLogger("serverwatch")

_STREAM_EDIT_INTERVAL_SECONDS = 0.8
_STREAM_TYPING_INTERVAL_SECONDS = 4.0
_TELEGRAM_MAX_TEXT_LENGTH = 4096
_CB_CONTEXT_INFO = "ctx_info"
_CB_CONTEXT_CLEAR = "ctx_clear"
_CB_CONTEXT_CLOSE = "ctx_close"

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

_STATUS_SOFT_TEMPLATE = """\
Preferred response structure for server status answers (soft template):
- Overall status: one short line with ✅/⚠️/❌ and the main reason.
- Key findings: 2-4 concise points using only relevant metrics.
- Recommended action: one practical next step, or "No immediate action needed".
- What to watch next: one short follow-up check.

Guidelines:
- This structure is preferred, not mandatory. Adapt naturally to the question.
- Omit sections that add no value.
- Keep wording varied; avoid repetitive phrasing across messages.
- Be specific with current metrics and avoid generic filler.
- Keep typical answers around 6-10 lines unless the situation is critical.
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


def _context_entry_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("chat.context_button_info", locale=locale),
                    callback_data=_CB_CONTEXT_INFO,
                )
            ]
        ]
    )


def _context_panel_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("chat.context_button_clear", locale=locale),
                    callback_data=_CB_CONTEXT_CLEAR,
                ),
                InlineKeyboardButton(
                    t("chat.context_button_close", locale=locale),
                    callback_data=_CB_CONTEXT_CLOSE,
                ),
            ]
        ]
    )


def _context_usage_text(locale: str, usage: store.ContextUsage) -> str:
    max_chars = max(1, usage.max_chars)
    percent = min(100, round((usage.used_chars / max_chars) * 100))
    return t(
        "chat.context_info",
        locale=locale,
        used_chars=usage.used_chars,
        max_chars=usage.max_chars,
        used_pct=percent,
        used_tokens=usage.used_tokens_estimate,
        max_tokens=usage.max_tokens_estimate,
        used_messages=usage.used_messages,
        max_messages=usage.max_messages,
        stored_messages=usage.stored_messages,
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


def _is_status_like_request(user_message: str) -> bool:
    """Best-effort detector for status/health intents in free-text chat."""
    text = user_message.casefold()
    return any(token in text for token in _GLANCES_HINTS)


def _append_status_template(system_prompt: str, *, enabled: bool) -> str:
    if not enabled:
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n{_STATUS_SOFT_TEMPLATE}"


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
    reply_markup: InlineKeyboardMarkup | None = None,
    allow_fallback_reply: bool = True,
) -> bool:
    """Try editing placeholder first; fallback to a new reply on Telegram failures."""
    if placeholder is not None:
        try:
            await placeholder.edit_text(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
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
        await source_message.reply_text(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
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

    cfg = get_config()
    locale = locale_from_update(update, fallback=cfg.bot_locale)
    chat = update.effective_chat
    if chat is None:
        return
    chat_id = chat.id

    await chat.send_action(ChatAction.TYPING)

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
    history = await store.get_chat_context_window(
        chat_id,
        max_turns=cfg.chat_context_max_turns,
        max_chars=cfg.chat_context_max_chars,
    )
    selection = await store.get_active_model()
    provider, _, _ = selection.partition(":")

    status_like_request = _is_status_like_request(message.text)
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
            system_prompt = _append_status_template(
                _SYSTEM_WITH_METRICS.format(
                    locale=locale,
                    metrics_json=snapshot.as_llm_context_json(),
                ),
                enabled=status_like_request,
            )
        else:
            system_prompt = _SYSTEM_NO_METRICS.format(locale=locale)
    except Exception:
        logger.warning("Could not fetch Glances snapshot for chat context")
        if use_glances:
            system_prompt = _append_status_template(
                _SYSTEM_METRICS_UNAVAILABLE.format(locale=locale),
                enabled=status_like_request,
            )
        else:
            system_prompt = _SYSTEM_NO_METRICS.format(locale=locale)

    # Query LLM (streaming when supported by provider)
    reply_accumulated = ""
    last_pushed = ""
    last_edit_at = 0.0
    last_typing_at = time.monotonic()
    stream_push_enabled = True

    try:
        async for chunk in llm_router.stream_chat(
            selection,
            system_prompt,
            message.text,
            history=history,
        ):
            if not chunk:
                continue

            reply_accumulated += chunk

            # Without a placeholder we avoid sending many partial replies.
            if placeholder is None:
                continue

            now = time.monotonic()
            candidate = _truncate_for_telegram(reply_accumulated)

            if (now - last_typing_at) >= _STREAM_TYPING_INTERVAL_SECONDS:
                await chat.send_action(ChatAction.TYPING)
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
    context_keyboard = _context_entry_keyboard(locale)

    if final_reply != last_pushed or placeholder is None:
        await _safe_edit_or_reply(
            source_message=message,
            placeholder=placeholder,
            text=final_reply,
            reply_markup=context_keyboard,
        )
    elif placeholder is not None:
        try:
            await placeholder.edit_reply_markup(reply_markup=context_keyboard)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("Could not attach context keyboard: %s", exc)
        except (TimedOut, NetworkError):
            logger.warning("Telegram timeout/network error while attaching context keyboard")
        except Exception:
            logger.exception("Unexpected error while attaching context keyboard")

    await store.append_chat_context_message(chat_id, "user", message.text)
    await store.append_chat_context_message(chat_id, "assistant", final_reply)


@restricted
async def cb_context_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    raw_message = query.message
    if raw_message is None:
        return
    message = cast(Message, raw_message)

    cfg = get_config()
    locale = locale_from_update(update, fallback=cfg.bot_locale)
    usage = await store.get_chat_context_usage(
        message.chat_id,
        max_turns=cfg.chat_context_max_turns,
        max_chars=cfg.chat_context_max_chars,
    )

    await message.reply_text(
        _context_usage_text(locale, usage),
        reply_markup=_context_panel_keyboard(locale),
    )


@restricted
async def context_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    cfg = get_config()
    locale = locale_from_update(update, fallback=cfg.bot_locale)
    usage = await store.get_chat_context_usage(
        chat.id,
        max_turns=cfg.chat_context_max_turns,
        max_chars=cfg.chat_context_max_chars,
    )
    await message.reply_text(
        _context_usage_text(locale, usage),
        reply_markup=_context_panel_keyboard(locale),
    )


@restricted
async def cb_context_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    raw_message = query.message
    if raw_message is None:
        return
    message = cast(Message, raw_message)

    locale = locale_from_update(update, fallback=get_config().bot_locale)
    await store.clear_chat_context(message.chat_id)
    await query.edit_message_text(
        t("chat.context_cleared", locale=locale),
        reply_markup=_context_panel_keyboard(locale),
    )


@restricted
async def cb_context_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    raw_message = query.message
    if raw_message is None:
        return
    message = cast(Message, raw_message)
    try:
        await message.delete()
    except Exception:
        logger.warning("Could not delete context panel message", exc_info=True)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("context", context_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            chat_handler,
        )
    )
    app.add_handler(CallbackQueryHandler(cb_context_info, pattern=f"^{_CB_CONTEXT_INFO}$"))
    app.add_handler(CallbackQueryHandler(cb_context_clear, pattern=f"^{_CB_CONTEXT_CLEAR}$"))
    app.add_handler(CallbackQueryHandler(cb_context_close, pattern=f"^{_CB_CONTEXT_CLOSE}$"))
