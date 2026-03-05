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
from telegram.constants import ChatAction, ParseMode
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
from app.services import ollama
from app.utils.i18n import t

logger = logging.getLogger("serverwatch")

# Callback data prefixes — kept short to stay within Telegram's 64-byte limit
_CB_SELECT = "mdl_sel:"  # mdl_sel:<model_name>
_CB_CONFIRM = "mdl_ok:"  # mdl_ok:<model_name>
_CB_CANCEL = "mdl_cancel"


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------


def _models_keyboard(models: list[str], active: str) -> InlineKeyboardMarkup:
    """One button per non-active model to trigger the selection flow."""
    rows = [
        [InlineKeyboardButton(name, callback_data=f"{_CB_SELECT}{name}")]
        for name in models
        if name != active
    ]
    return InlineKeyboardMarkup(rows)


def _confirm_keyboard(model: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("models.confirm_button"), callback_data=f"{_CB_CONFIRM}{model}"
                ),
                InlineKeyboardButton(t("models.cancel_button"), callback_data=_CB_CANCEL),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Shared rendering helper
# ---------------------------------------------------------------------------


async def _show_models(update: Update, edit: bool = False) -> None:
    """Fetch models and active model, then send or edit the message."""
    try:
        models = await ollama.list_models()
        active = await store.get_active_model()
    except Exception:
        logger.exception("Failed to fetch models")
        text = t("models.unavailable")
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        elif update.effective_message:
            await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    if not models:
        text = f"{t('models.header')}\n\n{t('models.no_models')}"
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        elif update.effective_message:
            await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    lines = [t("models.header"), ""]
    for name in models:
        marker = "✅" if name == active else "•"
        lines.append(f"{marker} `{name}`")

    text = "\n".join(lines)
    keyboard = _models_keyboard(models, active)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )


# ---------------------------------------------------------------------------
# Command / keyboard button handlers
# ---------------------------------------------------------------------------


@restricted
async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /models command."""
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _show_models(update)


@restricted
async def models_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 🤖 Models persistent keyboard button."""
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _show_models(update)


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

    model = (query.data or "")[len(_CB_SELECT) :]
    active = await store.get_active_model()

    if model == active:
        await query.edit_message_text(
            t("models.already_active", model=model), parse_mode=ParseMode.MARKDOWN
        )
        return

    await query.edit_message_text(
        t("models.confirm_change", model=model),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_confirm_keyboard(model),
    )


@restricted
async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User confirmed a model change — persist and acknowledge."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    model = (query.data or "")[len(_CB_CONFIRM) :]
    await store.set_active_model(model)
    logger.info("Active model changed to %s", model)

    await query.edit_message_text(t("models.updated", model=model), parse_mode=ParseMode.MARKDOWN)


@restricted
async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User cancelled the confirmation."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text(t("models.cancelled"), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app: Application) -> None:
    """Register all models-related handlers on the Application."""
    app.add_handler(CommandHandler("models", models_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^🤖 Models$") & ~filters.COMMAND,
            models_button,
        )
    )
    app.add_handler(CallbackQueryHandler(cb_select, pattern=f"^{_CB_SELECT}"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=f"^{_CB_CONFIRM}"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern=f"^{_CB_CANCEL}$"))
