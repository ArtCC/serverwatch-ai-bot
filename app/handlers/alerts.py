"""Handler for /alerts — view and edit alert thresholds.

Flow:
  1. /alerts or 🔔 Alerts button  → show current thresholds + Edit inline buttons.
  2. ✏️ Edit <metric>             → prompt user to type a new value (ConversationHandler).
  3. User sends a number          → show Confirm / Cancel.
  4. Confirm                      → persist; show updated thresholds.
  5. Cancel                       → cancelled message.
"""

from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.core import store
from app.core.auth import restricted
from app.utils.i18n import t

logger = logging.getLogger("serverwatch")

# ConversationHandler states
_AWAIT_VALUE = 1

# Callback data
_CB_EDIT_CPU = "alrt_edit_cpu"
_CB_EDIT_RAM = "alrt_edit_ram"
_CB_EDIT_DISK = "alrt_edit_disk"
_CB_CONFIRM = "alrt_ok:"  # alrt_ok:<metric>:<value>
_CB_CANCEL = "alrt_cancel"

# context.user_data keys
_UD_METRIC = "alrt_metric"
_UD_VALUE = "alrt_value"

_METRICS = {
    _CB_EDIT_CPU: "CPU",
    _CB_EDIT_RAM: "RAM",
    _CB_EDIT_DISK: "Disk",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _thresholds_text() -> str:
    cpu = await store.get_threshold_cpu()
    ram = await store.get_threshold_ram()
    disk = await store.get_threshold_disk()
    lines = [
        t("alerts.header"),
        "",
        t("alerts.threshold_line", metric=t("alerts.cpu_label"), value=int(cpu)),
        t("alerts.threshold_line", metric=t("alerts.ram_label"), value=int(ram)),
        t("alerts.threshold_line", metric=t("alerts.disk_label"), value=int(disk)),
    ]
    return "\n".join(lines)


def _edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("alerts.edit_button", metric=t("alerts.cpu_label")),
                    callback_data=_CB_EDIT_CPU,
                ),
                InlineKeyboardButton(
                    t("alerts.edit_button", metric=t("alerts.ram_label")),
                    callback_data=_CB_EDIT_RAM,
                ),
                InlineKeyboardButton(
                    t("alerts.edit_button", metric=t("alerts.disk_label")),
                    callback_data=_CB_EDIT_DISK,
                ),
            ]
        ]
    )


def _confirm_keyboard(metric: str, value: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("alerts.confirm_button"),
                    callback_data=f"{_CB_CONFIRM}{metric}:{value}",
                ),
                InlineKeyboardButton(t("alerts.cancel_button"), callback_data=_CB_CANCEL),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Entry point — show thresholds
# ---------------------------------------------------------------------------


async def _show_alerts(update: Update) -> None:
    text = await _thresholds_text()
    keyboard = _edit_keyboard()
    if update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )


@restricted
async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _show_alerts(update)


@restricted
async def alerts_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _show_alerts(update)


# ---------------------------------------------------------------------------
# Inline: edit request
# ---------------------------------------------------------------------------


@restricted
async def cb_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    metric = _METRICS.get(query.data or "", "")
    if context.user_data is not None:
        context.user_data[_UD_METRIC] = metric

    await query.edit_message_text(
        t("alerts.prompt_new_value", metric=metric),
        parse_mode=ParseMode.MARKDOWN,
    )
    return _AWAIT_VALUE


# ---------------------------------------------------------------------------
# Conversation: receive the numeric value
# ---------------------------------------------------------------------------


@restricted
async def receive_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if message is None or message.text is None:
        return ConversationHandler.END

    raw = message.text.strip()
    metric = (context.user_data or {}).get(_UD_METRIC, "")

    try:
        value = float(raw)
        if not (0 <= value <= 100):
            raise ValueError
    except ValueError:
        await message.reply_text(t("alerts.invalid_value"), parse_mode=ParseMode.MARKDOWN)
        return _AWAIT_VALUE

    if context.user_data is not None:
        context.user_data[_UD_VALUE] = value

    await message.reply_text(
        t("alerts.confirm_change", metric=metric, value=int(value)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_confirm_keyboard(metric, value),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Inline: confirm / cancel
# ---------------------------------------------------------------------------


@restricted
async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    data = (query.data or "")[len(_CB_CONFIRM) :]
    metric, _, raw_value = data.partition(":")
    value = float(raw_value)

    if metric == "CPU":
        await store.set_threshold_cpu(value)
    elif metric == "RAM":
        await store.set_threshold_ram(value)
    elif metric == "Disk":
        await store.set_threshold_disk(value)

    logger.info("Threshold updated: %s = %s%%", metric, value)

    updated_text = await _thresholds_text()
    await query.edit_message_text(
        t("alerts.updated", metric=metric, value=int(value)) + "\n\n" + updated_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_edit_keyboard(),
    )


@restricted
async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await query.edit_message_text(t("alerts.cancelled"), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app: Application) -> None:
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                cb_edit,
                pattern=f"^({_CB_EDIT_CPU}|{_CB_EDIT_RAM}|{_CB_EDIT_DISK})$",
            ),
        ],
        states={
            _AWAIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_value),
            ],
        },
        fallbacks=[],
        per_message=False,
    )

    app.add_handler(CommandHandler("alerts", alerts_command))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(f"^{re.escape(t('keyboard.alerts'))}$") & ~filters.COMMAND,
            alerts_button,
        )
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=f"^{_CB_CONFIRM}"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern=f"^{_CB_CANCEL}$"))
