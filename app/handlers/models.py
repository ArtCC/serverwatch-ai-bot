"""Handler for /models — list and select Ollama models.

Flow:
  1. /models or 🤖 Models button  → list models; active one marked ✅ in text;
                                     inline buttons for each non-active model.
  2. Press a model button         → show confirmation (Confirm / Cancel).
  3. Confirm                      → persist new active model; success message.
  4. Cancel                       → cancelled message.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
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
from app.services import ollama
from app.utils.i18n import locale_from_update, regex_for_key, t, text_matches_key

logger = logging.getLogger("serverwatch")

# Callback data prefixes — kept short to stay within Telegram's 64-byte limit
_CB_SELECT = "mdl_sel:"  # mdl_sel:<token>
_CB_CONFIRM = "mdl_ok:"  # mdl_ok:<token>
_CB_CANCEL = "mdl_cancel"
_UD_CHOICES = "mdl_choices"


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------


def _models_keyboard(models: list[str], active: str) -> tuple[InlineKeyboardMarkup, dict[str, str]]:
    """One button per non-active model to trigger the selection flow."""
    rows: list[list[InlineKeyboardButton]] = []
    mapping: dict[str, str] = {}
    idx = 0

    for name in models:
        if name == active:
            continue
        token = str(idx)
        mapping[token] = name
        rows.append([InlineKeyboardButton(name, callback_data=f"{_CB_SELECT}{token}")])
        idx += 1

    return InlineKeyboardMarkup(rows), mapping


def _confirm_keyboard(locale: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("models.confirm_button", locale=locale),
                    callback_data=f"{_CB_CONFIRM}{token}",
                ),
                InlineKeyboardButton(
                    t("models.cancel_button", locale=locale),
                    callback_data=_CB_CANCEL,
                ),
            ]
        ]
    )


def _resolve_model_token(context: ContextTypes.DEFAULT_TYPE, token: str) -> str | None:
    user_data = context.user_data or {}
    choices = user_data.get(_UD_CHOICES)
    if not isinstance(choices, dict):
        return None
    model = choices.get(token)
    return model if isinstance(model, str) else None


# ---------------------------------------------------------------------------
# Shared rendering helper
# ---------------------------------------------------------------------------


async def _show_models(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> None:
    """Fetch models and active model, then send or edit the message."""
    locale = locale_from_update(update, fallback=get_config().bot_locale)

    try:
        models = await ollama.list_models()
        active = await store.get_active_model()
    except Exception:
        logger.exception("Failed to fetch models")
        text = t("models.unavailable", locale=locale)
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text)
        elif update.effective_message:
            await update.effective_message.reply_text(text)
        return

    if not models:
        text = f"{t('models.header', locale=locale)}\n\n{t('models.no_models', locale=locale)}"
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text)
        elif update.effective_message:
            await update.effective_message.reply_text(text)
        return

    lines = [t("models.header", locale=locale), ""]
    for name in models:
        marker = "✅" if name == active else "•"
        lines.append(f"{marker} {name}")

    text = "\n".join(lines)
    keyboard, mapping = _models_keyboard(models, active)
    if context.user_data is not None:
        context.user_data[_UD_CHOICES] = mapping

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Command / keyboard button handlers
# ---------------------------------------------------------------------------


@restricted
async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /models command."""
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _show_models(update, context)


@restricted
async def models_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 🤖 Models persistent keyboard button."""
    message = update.effective_message
    if message is None or message.text is None:
        return
    if not text_matches_key(message.text, "keyboard.models"):
        return

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _show_models(update, context)


# ---------------------------------------------------------------------------
# Inline callback handlers
# ---------------------------------------------------------------------------


@restricted
async def cb_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User pressed a model button — show confirmation."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    locale = locale_from_update(update, fallback=get_config().bot_locale)

    token = (query.data or "")[len(_CB_SELECT) :]
    model = _resolve_model_token(context, token)
    if model is None:
        await query.edit_message_text(t("models.selection_expired", locale=locale))
        return

    active = await store.get_active_model()

    if model == active:
        await query.edit_message_text(t("models.already_active", locale=locale, model=model))
        return

    await query.edit_message_text(
        t("models.confirm_change", locale=locale, model=model),
        reply_markup=_confirm_keyboard(locale, token),
    )


@restricted
async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User confirmed a model change — persist and acknowledge."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    locale = locale_from_update(update, fallback=get_config().bot_locale)

    token = (query.data or "")[len(_CB_CONFIRM) :]
    model = _resolve_model_token(context, token)
    if model is None:
        await query.edit_message_text(t("models.selection_expired", locale=locale))
        return

    await store.set_active_model(model)
    logger.info("Active model changed to %s", model)

    await query.edit_message_text(t("models.updated", locale=locale, model=model))


@restricted
async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User cancelled the confirmation."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    await query.edit_message_text(t("models.cancelled", locale=locale))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app: Application) -> None:
    """Register all models-related handlers on the Application."""
    app.add_handler(CommandHandler("models", models_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(regex_for_key("keyboard.models")) & ~filters.COMMAND,
            models_button,
        )
    )
    app.add_handler(CallbackQueryHandler(cb_select, pattern=f"^{_CB_SELECT}"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=f"^{_CB_CONFIRM}"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern=f"^{_CB_CANCEL}$"))
