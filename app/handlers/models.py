"""Handler for /models — list and select Ollama/cloud model options.

Flow:
  1. /models or 🤖 Models button  → list models; active one marked ✅ in text;
                                     inline buttons for each non-active model.
  2. Press a model button         → show confirmation (Confirm / Cancel).
  3. Confirm                      → persist new active model; success message.
  4. Cancel                       → cancelled message.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import cast

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
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
from app.services.llm_router import ModelOption, configured_cloud_options
from app.utils.i18n import locale_from_update, regex_for_key, t, text_matches_key

logger = logging.getLogger("serverwatch")

# Callback data prefixes — kept short to stay within Telegram's 64-byte limit
_CB_SELECT = "mdl_sel:"  # mdl_sel:<token>
_CB_CONFIRM = "mdl_ok:"  # mdl_ok:<token>
_CB_CANCEL = "mdl_cancel"
_CB_CLOSE = "mdl_close"
_CB_CHANGE = "mdl_change"
_CB_INSTALL_PROMPT = "mdl_install_prompt"
_CB_INSTALL_CANCEL = "mdl_install_cancel"
_CB_DELETE_MENU = "mdl_delete_menu"
_CB_DELETE_SELECT = "mdl_del_sel:"
_CB_DELETE_CONFIRM = "mdl_del_ok:"
_CB_DELETE_BACK = "mdl_del_back"
_UD_CHOICES = "mdl_choices"
_UD_DELETE_CHOICES = "mdl_delete_choices"
_UD_INSTALL_PROMPT_MESSAGE_ID = "mdl_install_prompt_message_id"
_UD_INSTALL_CANCEL_EVENT = "mdl_install_cancel_event"


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------


def _provider_label(provider: str, locale: str) -> str:
    return {
        "ollama": t("models.provider_ollama", locale=locale),
        "openai": t("models.provider_openai", locale=locale),
        "anthropic": t("models.provider_anthropic", locale=locale),
        "deepseek": t("models.provider_deepseek", locale=locale),
    }.get(provider, provider)


def _display_name(option: ModelOption, locale: str) -> str:
    return f"{_provider_label(option.provider, locale)} | {option.model}"


def _option_short_name(option: ModelOption, locale: str) -> str:
    if option.provider == "ollama":
        return option.model
    return f"{_provider_label(option.provider, locale)} | {option.model}"


def _models_keyboard(
    options: list[ModelOption],
    active: str,
    locale: str,
) -> tuple[InlineKeyboardMarkup, dict[str, str]]:
    """One button per non-active model to trigger the selection flow."""
    rows: list[list[InlineKeyboardButton]] = []
    mapping: dict[str, str] = {}
    idx = 0

    for option in options:
        if option.selection == active:
            continue
        token = str(idx)
        mapping[token] = option.selection
        rows.append(
            [
                InlineKeyboardButton(
                    t(
                        "models.select_button",
                        locale=locale,
                        model=_option_short_name(option, locale),
                    ),
                    callback_data=f"{_CB_SELECT}{token}",
                )
            ]
        )
        idx += 1

    rows.append(
        [
            InlineKeyboardButton(
                t("models.cancel_button", locale=locale),
                callback_data=_CB_CLOSE,
            )
        ]
    )

    return InlineKeyboardMarkup(rows), mapping


def _overview_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("models.change_button", locale=locale),
                    callback_data=_CB_CHANGE,
                )
            ],
            [
                InlineKeyboardButton(
                    t("models.install_button", locale=locale),
                    callback_data=_CB_INSTALL_PROMPT,
                )
            ],
            [
                InlineKeyboardButton(
                    t("models.delete_button", locale=locale),
                    callback_data=_CB_DELETE_MENU,
                )
            ],
            [
                InlineKeyboardButton(
                    t("models.cancel_button", locale=locale),
                    callback_data=_CB_CLOSE,
                )
            ],
        ]
    )


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


def _delete_models_keyboard(
    local_models: list[str],
    locale: str,
) -> tuple[InlineKeyboardMarkup, dict[str, str]]:
    rows: list[list[InlineKeyboardButton]] = []
    mapping: dict[str, str] = {}
    for idx, model_name in enumerate(local_models):
        token = str(idx)
        mapping[token] = model_name
        rows.append(
            [
                InlineKeyboardButton(
                    t("models.delete_select_button", locale=locale, model=model_name),
                    callback_data=f"{_CB_DELETE_SELECT}{token}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                t("models.back_button", locale=locale),
                callback_data=_CB_DELETE_BACK,
            )
        ]
    )
    return InlineKeyboardMarkup(rows), mapping


def _delete_confirm_keyboard(locale: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("models.confirm_button", locale=locale),
                    callback_data=f"{_CB_DELETE_CONFIRM}{token}",
                ),
                InlineKeyboardButton(
                    t("models.cancel_button", locale=locale),
                    callback_data=_CB_DELETE_BACK,
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


def _resolve_delete_model_token(context: ContextTypes.DEFAULT_TYPE, token: str) -> str | None:
    user_data = context.user_data or {}
    choices = user_data.get(_UD_DELETE_CHOICES)
    if not isinstance(choices, dict):
        return None
    model = choices.get(token)
    return model if isinstance(model, str) else None


def _parse_selection(selection: str) -> tuple[str, str]:
    provider, _, model = selection.partition(":")
    return provider, model


def _build_models_text(
    options: list[ModelOption],
    active: str,
    locale: str,
    ollama_unavailable: bool,
) -> str:
    local_options = [o for o in options if o.provider == "ollama"]
    cloud_options = [o for o in options if o.provider != "ollama"]

    active_option = next((o for o in options if o.selection == active), None)
    if active_option is not None:
        active_label = _display_name(active_option, locale)
    else:
        provider, model = _parse_selection(active)
        active_label = f"{_provider_label(provider, locale)} | {model}"

    lines = [
        t("models.header", locale=locale),
        "",
        t("models.active_line", locale=locale, model=active_label),
        "",
        t("models.local_section", locale=locale),
    ]

    if local_options:
        for option in local_options:
            marker = "✅" if option.selection == active else "•"
            lines.append(f"{marker} {option.model}")
    else:
        lines.append(t("models.local_empty", locale=locale))

    if ollama_unavailable:
        lines.append(t("models.local_unavailable", locale=locale))

    lines.extend(["", t("models.cloud_section", locale=locale)])

    if cloud_options:
        for option in cloud_options:
            marker = "✅" if option.selection == active else "•"
            lines.append(f"{marker} {_display_name(option, locale)}")
    else:
        lines.append(t("models.cloud_empty", locale=locale))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared rendering helper
# ---------------------------------------------------------------------------


async def _show_models(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
    selection_mode: bool = False,
) -> None:
    """Fetch models and active model, then send or edit the message."""
    locale = locale_from_update(update, fallback=get_config().bot_locale)

    ollama_models: list[str] = []
    ollama_unavailable = False
    try:
        ollama_models = await ollama.list_models()
    except Exception:
        ollama_unavailable = True
        logger.exception("Failed to fetch Ollama models")

    active = await store.get_active_model()

    options: list[ModelOption] = [
        ModelOption(selection=f"ollama:{name}", provider="ollama", model=name)
        for name in ollama_models
    ]
    cloud_options = configured_cloud_options()
    options.extend(cloud_options)

    logger.info(
        "Model options loaded: ollama=%d cloud=%d total=%d",
        len(ollama_models),
        len(cloud_options),
        len(options),
    )

    if not options:
        text = (
            f"{t('models.header', locale=locale)}\n\n"
            f"{t('models.no_models_available', locale=locale)}"
        )
        if ollama_unavailable:
            text += f"\n\n{t('models.unavailable', locale=locale)}"
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text)
        elif update.effective_message:
            await update.effective_message.reply_text(text)
        return

    text = _build_models_text(options, active, locale, ollama_unavailable)
    if selection_mode:
        keyboard, mapping = _models_keyboard(options, active, locale)
        if context.user_data is not None:
            context.user_data[_UD_CHOICES] = mapping
    else:
        keyboard = _overview_keyboard(locale)
        if context.user_data is not None:
            context.user_data.pop(_UD_CHOICES, None)

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


@restricted
async def install_model_name_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Install a local Ollama model after replying with its name."""
    message = update.effective_message
    if message is None or message.text is None or message.reply_to_message is None:
        return
    if context.user_data is None:
        return

    prompt_message_id = context.user_data.get(_UD_INSTALL_PROMPT_MESSAGE_ID)
    if not isinstance(prompt_message_id, int):
        return
    if message.reply_to_message.message_id != prompt_message_id:
        return

    locale = locale_from_update(update, fallback=get_config().bot_locale)
    model_name = message.text.strip()
    if not model_name:
        await message.reply_text(t("models.install_invalid_name", locale=locale))
        return

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)

    status_message = await message.reply_text(
        t("models.install_started", locale=locale, model=model_name),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        t("models.install_cancel_button", locale=locale),
                        callback_data=_CB_INSTALL_CANCEL,
                    )
                ]
            ]
        ),
    )
    context.user_data.pop(_UD_INSTALL_PROMPT_MESSAGE_ID, None)
    cancel_event = context.user_data.get(_UD_INSTALL_CANCEL_EVENT)
    if isinstance(cancel_event, asyncio.Event):
        cancel_event.set()
    cancel_event = asyncio.Event()
    context.user_data[_UD_INSTALL_CANCEL_EVENT] = cancel_event

    last_edit = 0.0

    async def _on_progress(status: str, completed: int, total: int) -> None:
        nonlocal last_edit
        now = monotonic()
        if now - last_edit < 2.0:
            return
        last_edit = now

        if total > 0 and completed > 0:
            pct = completed / total
            filled = int(pct * 10)
            bar = "█" * filled + "░" * (10 - filled)
            mb_done = completed / 1_048_576
            mb_total = total / 1_048_576
            progress_line = f"{bar} {pct * 100:.0f}% ({mb_done:.1f}/{mb_total:.1f} MB)"
        else:
            progress_line = status or "..."

        try:
            await status_message.edit_text(
                t(
                    "models.install_progress",
                    locale=locale,
                    model=model_name,
                    progress=progress_line,
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                t("models.install_cancel_button", locale=locale),
                                callback_data=_CB_INSTALL_CANCEL,
                            )
                        ]
                    ]
                ),
            )
        except Exception:
            logger.debug("Could not update install progress message", exc_info=True)

    try:
        await ollama.pull_model(
            model_name,
            progress_callback=_on_progress,
            cancel_event=cancel_event,
        )
    except asyncio.CancelledError:
        await status_message.edit_text(
            t("models.install_cancelled", locale=locale, model=model_name),
            reply_markup=None,
        )
        return
    except Exception as error:
        logger.exception("Model install failed for %s", model_name)
        await status_message.edit_text(
            t(
                "models.install_failed",
                locale=locale,
                model=model_name,
                error=str(error),
            ),
            reply_markup=None,
        )
        return
    finally:
        context.user_data.pop(_UD_INSTALL_CANCEL_EVENT, None)

    await status_message.edit_text(
        t("models.install_done", locale=locale, model=model_name),
        reply_markup=None,
    )


@restricted
async def cb_install_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel an in-progress manual model download."""
    query = update.callback_query
    if query is None:
        return
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    cancel_event = None
    if context.user_data is not None:
        raw_event = context.user_data.get(_UD_INSTALL_CANCEL_EVENT)
        if isinstance(raw_event, asyncio.Event):
            cancel_event = raw_event
    if cancel_event is None:
        await query.answer(t("models.install_cancel_unavailable", locale=locale), show_alert=True)
        return
    cancel_event.set()
    await query.answer(t("models.install_cancel_requested", locale=locale), show_alert=False)


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
        provider, model_name = _parse_selection(model)
        await query.edit_message_text(
            t(
                "models.already_active",
                locale=locale,
                model=f"{_provider_label(provider, locale)} | {model_name}",
            )
        )
        return

    provider, model_name = _parse_selection(model)
    await query.edit_message_text(
        t(
            "models.confirm_change",
            locale=locale,
            model=f"{_provider_label(provider, locale)} | {model_name}",
        ),
        reply_markup=_confirm_keyboard(locale, token),
    )


@restricted
async def cb_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User requested model change — show selectable model buttons."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await _show_models(update, context, edit=True, selection_mode=True)


@restricted
async def cb_install_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask the user for the Ollama model name to install."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    locale = locale_from_update(update, fallback=get_config().bot_locale)
    if query.message is None:
        return

    prompt = await cast(Message, query.message).reply_text(
        t("models.install_prompt", locale=locale),
        reply_markup=ForceReply(selective=True),
    )
    if context.user_data is not None:
        context.user_data[_UD_INSTALL_PROMPT_MESSAGE_ID] = prompt.message_id


@restricted
async def cb_delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show local models to choose which one to delete."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    locale = locale_from_update(update, fallback=get_config().bot_locale)

    local_models = await ollama.list_models()
    if not local_models:
        await query.edit_message_text(
            t("models.delete_no_local_models", locale=locale),
            reply_markup=_overview_keyboard(locale),
        )
        return

    keyboard, mapping = _delete_models_keyboard(local_models, locale)
    if context.user_data is not None:
        context.user_data[_UD_DELETE_CHOICES] = mapping
    await query.edit_message_text(
        t("models.delete_choose", locale=locale),
        reply_markup=keyboard,
    )


@restricted
async def cb_delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask confirmation for deleting the selected local model."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    locale = locale_from_update(update, fallback=get_config().bot_locale)

    token = (query.data or "")[len(_CB_DELETE_SELECT) :]
    model_name = _resolve_delete_model_token(context, token)
    if model_name is None:
        await query.edit_message_text(t("models.selection_expired", locale=locale))
        return

    await query.edit_message_text(
        t("models.delete_confirm", locale=locale, model=model_name),
        reply_markup=_delete_confirm_keyboard(locale, token),
    )


@restricted
async def cb_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete selected local model and adjust active model if needed."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    locale = locale_from_update(update, fallback=get_config().bot_locale)

    token = (query.data or "")[len(_CB_DELETE_CONFIRM) :]
    model_name = _resolve_delete_model_token(context, token)
    if model_name is None:
        await query.edit_message_text(t("models.selection_expired", locale=locale))
        return

    try:
        await ollama.delete_model(model_name)
    except Exception as error:
        logger.exception("Model delete failed for %s", model_name)
        await query.edit_message_text(
            t("models.delete_failed", locale=locale, model=model_name, error=str(error)),
            reply_markup=_overview_keyboard(locale),
        )
        return

    active = await store.get_active_model()
    if active == f"ollama:{model_name}":
        remaining_locals = [name for name in await ollama.list_models() if name != model_name]
        if remaining_locals:
            await store.set_active_model(f"ollama:{remaining_locals[0]}")
        else:
            cloud_options = configured_cloud_options()
            if cloud_options:
                await store.set_active_model(cloud_options[0].selection)

    await query.edit_message_text(
        t("models.delete_done", locale=locale, model=model_name),
        reply_markup=_overview_keyboard(locale),
    )


@restricted
async def cb_delete_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return to the models overview panel."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    await _show_models(update, context, edit=True)


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

    provider, model_name = _parse_selection(model)
    await query.edit_message_text(
        t(
            "models.updated",
            locale=locale,
            model=f"{_provider_label(provider, locale)} | {model_name}",
        )
    )


@restricted
async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User cancelled the confirmation."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    await query.edit_message_text(t("models.cancelled", locale=locale))


@restricted
async def cb_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close the models panel by deleting the current message."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if query.message is None:
        return
    try:
        await cast(Message, query.message).delete()
    except Exception:
        logger.warning("Could not delete models message on close", exc_info=True)


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
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.REPLY & ~filters.COMMAND,
            install_model_name_reply,
        )
    )
    app.add_handler(CallbackQueryHandler(cb_select, pattern=f"^{_CB_SELECT}"))
    app.add_handler(CallbackQueryHandler(cb_change, pattern=f"^{_CB_CHANGE}$"))
    app.add_handler(CallbackQueryHandler(cb_install_prompt, pattern=f"^{_CB_INSTALL_PROMPT}$"))
    app.add_handler(CallbackQueryHandler(cb_install_cancel, pattern=f"^{_CB_INSTALL_CANCEL}$"))
    app.add_handler(CallbackQueryHandler(cb_delete_menu, pattern=f"^{_CB_DELETE_MENU}$"))
    app.add_handler(CallbackQueryHandler(cb_delete_select, pattern=f"^{_CB_DELETE_SELECT}"))
    app.add_handler(CallbackQueryHandler(cb_delete_confirm, pattern=f"^{_CB_DELETE_CONFIRM}"))
    app.add_handler(CallbackQueryHandler(cb_delete_back, pattern=f"^{_CB_DELETE_BACK}$"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=f"^{_CB_CONFIRM}"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern=f"^{_CB_CANCEL}$"))
    app.add_handler(CallbackQueryHandler(cb_close, pattern=f"^{_CB_CLOSE}$"))
