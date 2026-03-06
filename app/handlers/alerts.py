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
from typing import cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
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
from app.core.config import get_config
from app.utils.i18n import locale_from_update, regex_for_key, t, text_matches_key

logger = logging.getLogger("serverwatch")

# ConversationHandler states
_AWAIT_VALUE = 1

# Callback data
_CB_EDIT_CPU = "alrt_edit_cpu"
_CB_EDIT_RAM = "alrt_edit_ram"
_CB_EDIT_DISK = "alrt_edit_disk"
_CB_CONFIRM = "alrt_ok:"  # alrt_ok:<metric>:<value>
_CB_CANCEL = "alrt_cancel"
_CB_CLOSE = "alrt_close"

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


async def _thresholds_text(locale: str) -> str:
    cpu, ram, disk = await store.get_thresholds()
    lines = [
        t("alerts.header", locale=locale),
        "",
        t(
            "alerts.threshold_line",
            locale=locale,
            metric=t("alerts.cpu_label", locale=locale),
            value=int(cpu),
        ),
        t(
            "alerts.threshold_line",
            locale=locale,
            metric=t("alerts.ram_label", locale=locale),
            value=int(ram),
        ),
        t(
            "alerts.threshold_line",
            locale=locale,
            metric=t("alerts.disk_label", locale=locale),
            value=int(disk),
        ),
    ]
    return "\n".join(lines)


def _edit_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(
                        "alerts.edit_button",
                        locale=locale,
                        metric=t("alerts.cpu_label", locale=locale),
                    ),
                    callback_data=_CB_EDIT_CPU,
                ),
                InlineKeyboardButton(
                    t(
                        "alerts.edit_button",
                        locale=locale,
                        metric=t("alerts.ram_label", locale=locale),
                    ),
                    callback_data=_CB_EDIT_RAM,
                ),
                InlineKeyboardButton(
                    t(
                        "alerts.edit_button",
                        locale=locale,
                        metric=t("alerts.disk_label", locale=locale),
                    ),
                    callback_data=_CB_EDIT_DISK,
                ),
            ],
            [
                InlineKeyboardButton(
                    t("alerts.cancel_button", locale=locale),
                    callback_data=_CB_CLOSE,
                )
            ],
        ]
    )


def _confirm_keyboard(metric: str, value: float, locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("alerts.confirm_button", locale=locale),
                    callback_data=f"{_CB_CONFIRM}{metric}:{value}",
                ),
                InlineKeyboardButton(
                    t("alerts.cancel_button", locale=locale),
                    callback_data=_CB_CANCEL,
                ),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Entry point — show thresholds
# ---------------------------------------------------------------------------


async def _show_alerts(update: Update) -> None:
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    text = await _thresholds_text(locale)
    keyboard = _edit_keyboard(locale)
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
    message = update.effective_message
    if message is None or message.text is None:
        return
    if not text_matches_key(message.text, "keyboard.alerts"):
        return

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
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    metric_label = {
        "CPU": t("alerts.cpu_label", locale=locale),
        "RAM": t("alerts.ram_label", locale=locale),
        "Disk": t("alerts.disk_label", locale=locale),
    }.get(metric, metric)
    if context.user_data is not None:
        context.user_data[_UD_METRIC] = metric

    await query.edit_message_text(
        t("alerts.prompt_new_value", locale=locale, metric=metric_label),
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
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    metric = (context.user_data or {}).get(_UD_METRIC, "")
    metric_label = {
        "CPU": t("alerts.cpu_label", locale=locale),
        "RAM": t("alerts.ram_label", locale=locale),
        "Disk": t("alerts.disk_label", locale=locale),
    }.get(metric, metric)

    try:
        value = float(raw)
        if not (0 <= value <= 100):
            raise ValueError
    except ValueError:
        await message.reply_text(
            t("alerts.invalid_value", locale=locale),
            parse_mode=ParseMode.MARKDOWN,
        )
        return _AWAIT_VALUE

    if context.user_data is not None:
        context.user_data[_UD_VALUE] = value

    await message.reply_text(
        t("alerts.confirm_change", locale=locale, metric=metric_label, value=int(value)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_confirm_keyboard(metric, value, locale),
    )
    return ConversationHandler.END


def _parse_confirm_payload(data: str) -> tuple[str, float] | None:
    if not data.startswith(_CB_CONFIRM):
        return None
    payload = data[len(_CB_CONFIRM) :]
    metric, _, raw_value = payload.partition(":")
    if metric not in {"CPU", "RAM", "Disk"}:
        return None
    try:
        value = float(raw_value)
    except ValueError:
        return None
    if not (0 <= value <= 100):
        return None
    return metric, value


# ---------------------------------------------------------------------------
# Inline: confirm / cancel
# ---------------------------------------------------------------------------


@restricted
async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    locale = locale_from_update(update, fallback=get_config().bot_locale)
    parsed = _parse_confirm_payload(query.data or "")
    if parsed is None:
        await query.edit_message_text(
            t("alerts.invalid_value", locale=locale),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    metric, value = parsed

    if metric == "CPU":
        await store.set_threshold_cpu(value)
    elif metric == "RAM":
        await store.set_threshold_ram(value)
    elif metric == "Disk":
        await store.set_threshold_disk(value)

    logger.info("Threshold updated: %s = %s%%", metric, value)

    updated_text = await _thresholds_text(locale)
    metric_label = {
        "CPU": t("alerts.cpu_label", locale=locale),
        "RAM": t("alerts.ram_label", locale=locale),
        "Disk": t("alerts.disk_label", locale=locale),
    }.get(metric, metric)
    await query.edit_message_text(
        t("alerts.updated", locale=locale, metric=metric_label, value=int(value))
        + "\n\n"
        + updated_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_edit_keyboard(locale),
    )


@restricted
async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    await query.edit_message_text(
        t("alerts.cancelled", locale=locale),
        parse_mode=ParseMode.MARKDOWN,
    )


@restricted
async def cb_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close the alerts panel by deleting the current message."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if query.message is None:
        return
    try:
        await cast(Message, query.message).delete()
    except Exception:
        logger.warning("Could not delete alerts message on close", exc_info=True)


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
            filters.TEXT & filters.Regex(regex_for_key("keyboard.alerts")) & ~filters.COMMAND,
            alerts_button,
        )
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=f"^{_CB_CONFIRM}"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern=f"^{_CB_CANCEL}$"))
    app.add_handler(CallbackQueryHandler(cb_close, pattern=f"^{_CB_CLOSE}$"))
