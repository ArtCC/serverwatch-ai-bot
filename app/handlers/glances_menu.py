"""Handler for /glances — on-demand, per-endpoint Glances details.

Flow:
  1. /glances or inline open button  -> show detail menu.
  2. User picks one endpoint          -> fetch that endpoint live now.
  3. Show endpoint payload + actions  -> Refresh / Back / Close.
"""

from __future__ import annotations

import json
import logging
import time
from typing import cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.core import store
from app.core.auth import restricted
from app.core.config import get_config
from app.services import glances, llm_router
from app.utils.i18n import locale_from_update, t

logger = logging.getLogger("serverwatch")

_CB_OPEN = "glc_open"
_CB_SELECT_PREFIX = "glc_sel:"
_CB_REFRESH_PREFIX = "glc_ref:"
_CB_BACK = "glc_back"
_CB_CLOSE = "glc_close"

_MENU_KEYS: tuple[str, ...] = (
    "cpu",
    "mem",
    "fs",
    "load",
    "network",
    "containers",
    "processlist",
    "uptime",
    "system",
    "sensors",
)

_TELEGRAM_MAX_TEXT_LENGTH = 4096
_TELEGRAM_HARD_FALLBACK_LENGTH = 1000
_MAX_DETAIL_JSON_FOR_LLM = 5000
_STREAM_EDIT_INTERVAL_SECONDS = 0.8

_DETAIL_SUMMARY_SYSTEM = """\
You are ServerWatch, a concise server monitoring assistant.
You are given live metrics from one Glances endpoint and must summarize them for Telegram.

Rules:
- Always reply in the user's language (locale: {locale}).
- Plain text only (no markdown, no code blocks, no JSON).
- Keep it short and practical (4-8 lines).
- Start with one status line: good / warning / critical.
- Mention only the most relevant facts from the provided data.
- End with one actionable recommendation or "No immediate action needed".
- Never invent metrics that are not present.
"""


def open_menu_callback_data() -> str:
    """Expose callback data so other handlers can open this menu inline."""
    return _CB_OPEN


def _label_for_key(key: str, locale: str) -> str:
    labels = {
        "cpu": t("glances.options.cpu", locale=locale),
        "mem": t("glances.options.mem", locale=locale),
        "fs": t("glances.options.fs", locale=locale),
        "load": t("glances.options.load", locale=locale),
        "network": t("glances.options.network", locale=locale),
        "containers": t("glances.options.containers", locale=locale),
        "processlist": t("glances.options.processlist", locale=locale),
        "uptime": t("glances.options.uptime", locale=locale),
        "system": t("glances.options.system", locale=locale),
        "sensors": t("glances.options.sensors", locale=locale),
    }
    return labels.get(key, key)


def _fit_for_telegram(text: str) -> str:
    if len(text) <= _TELEGRAM_MAX_TEXT_LENGTH:
        return text
    suffix = "\n\n...[truncated]"
    max_body = _TELEGRAM_MAX_TEXT_LENGTH - len(suffix)
    if max_body <= 0:
        return text[:_TELEGRAM_MAX_TEXT_LENGTH]
    return text[:max_body] + suffix


def _serialize_for_llm(payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=True)
    if len(serialized) <= _MAX_DETAIL_JSON_FOR_LLM:
        return serialized
    suffix = "...[truncated]"
    return serialized[: _MAX_DETAIL_JSON_FOR_LLM - len(suffix)] + suffix


def _compact_payload(
    payload: object,
    *,
    depth: int = 0,
    max_depth: int = 3,
    max_dict_keys: int = 25,
    max_list_items: int = 8,
) -> object:
    """Reduce large endpoint payloads before sending them to the LLM."""
    if depth >= max_depth:
        if isinstance(payload, str):
            return payload[:200]
        if isinstance(payload, (int, float, bool)) or payload is None:
            return payload
        return str(type(payload).__name__)

    if isinstance(payload, dict):
        compact: dict[str, object] = {}
        for idx, (key, value) in enumerate(payload.items()):
            if idx >= max_dict_keys:
                compact["_truncated_keys"] = len(payload) - max_dict_keys
                break
            compact[str(key)] = _compact_payload(
                value,
                depth=depth + 1,
                max_depth=max_depth,
                max_dict_keys=max_dict_keys,
                max_list_items=max_list_items,
            )
        return compact

    if isinstance(payload, list):
        compact_list: list[object] = []
        for idx, item in enumerate(payload):
            if idx >= max_list_items:
                compact_list.append({"_truncated_items": len(payload) - max_list_items})
                break
            compact_list.append(
                _compact_payload(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_dict_keys=max_dict_keys,
                    max_list_items=max_list_items,
                )
            )
        return compact_list

    if isinstance(payload, str):
        return payload[:400]

    return payload


def _round_num(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 2)
    return value


def _pick_fields(source: dict[str, object], keys: tuple[str, ...]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in keys:
        if key in source:
            result[key] = _round_num(source[key])
    return result


def _prepare_llm_payload(key: str, payload: object) -> object:
    """Build a tiny endpoint-aware payload for faster summarization."""
    if isinstance(payload, dict):
        if key == "cpu":
            return _pick_fields(
                payload,
                (
                    "total",
                    "user",
                    "system",
                    "idle",
                    "iowait",
                    "irq",
                    "softirq",
                    "steal",
                ),
            )
        if key == "mem":
            return _pick_fields(
                payload,
                (
                    "total",
                    "used",
                    "free",
                    "percent",
                    "available",
                    "active",
                    "inactive",
                    "cached",
                    "buffers",
                ),
            )
        if key == "load":
            return _pick_fields(payload, ("min1", "min5", "min15", "cpucore"))
        if key == "system":
            return _pick_fields(
                payload,
                (
                    "hostname",
                    "os_name",
                    "os_version",
                    "platform",
                    "linux_distro",
                    "hr_name",
                ),
            )
        if key == "uptime":
            return payload
        return _compact_payload(payload, max_depth=2, max_dict_keys=15, max_list_items=6)

    if isinstance(payload, list):
        items: list[object] = []
        max_items = 5
        for item in payload[:max_items]:
            if not isinstance(item, dict):
                items.append(_round_num(item))
                continue

            if key == "fs":
                items.append(
                    _pick_fields(item, ("mnt_point", "device_name", "size", "used", "percent"))
                )
                continue
            if key == "network":
                items.append(
                    _pick_fields(
                        item,
                        (
                            "interface_name",
                            "is_up",
                            "speed",
                            "time_since_update",
                            "bytes_recv_rate_per_sec",
                            "bytes_sent_rate_per_sec",
                            "bytes_all_rate_per_sec",
                            "bytes_recv_gauge",
                            "bytes_sent_gauge",
                        ),
                    )
                )
                continue
            if key == "containers":
                items.append(
                    _pick_fields(
                        item,
                        (
                            "name",
                            "status",
                            "cpu_percent",
                            "memory_usage",
                            "memory_limit",
                            "io_rx",
                            "io_wx",
                            "network_rx",
                            "network_tx",
                        ),
                    )
                )
                continue
            if key == "processlist":
                items.append(
                    _pick_fields(
                        item,
                        (
                            "name",
                            "pid",
                            "cpu_percent",
                            "memory_percent",
                            "status",
                            "username",
                            "num_threads",
                        ),
                    )
                )
                continue
            if key == "sensors":
                items.append(
                    _pick_fields(item, ("label", "type", "value", "unit", "warning", "critical"))
                )
                continue

            items.append(_compact_payload(item, max_depth=2, max_dict_keys=12, max_list_items=4))

        if len(payload) > max_items:
            items.append({"_truncated_items": len(payload) - max_items})
        return items

    return _round_num(payload)


def _fallback_summary(locale: str, label: str, payload: object) -> str:
    header = t("glances.detail_header", locale=locale, label=label)
    lines = [header]

    if isinstance(payload, dict):
        scalar_items: list[tuple[str, object]] = []
        for key, value in payload.items():
            if isinstance(value, (str, int, float, bool)):
                scalar_items.append((str(key), value))
        if scalar_items:
            lines.append("")
            lines.append("Summary")
            for key, value in scalar_items[:6]:
                lines.append(f"- {key}: {value}")
        else:
            lines.append("")
            lines.append("No compact scalar metrics available for this endpoint right now.")
    elif isinstance(payload, list):
        lines.append("")
        lines.append(f"Items returned: {len(payload)}")
        if payload and isinstance(payload[0], dict):
            first = payload[0]
            name = first.get("name") if isinstance(first, dict) else None
            if name:
                lines.append(f"First item: {name}")
    else:
        lines.append("")
        lines.append(f"Value: {payload}")

    lines.append("")
    lines.append("No immediate action needed")
    return "\n".join(lines)


async def _summarize_payload(*, locale: str, label: str, key: str, payload: object) -> str:
    selection = await store.get_active_model()
    system_prompt = _DETAIL_SUMMARY_SYSTEM.format(locale=locale)
    llm_payload = _prepare_llm_payload(key, payload)
    user_prompt = (
        f"Endpoint key: {key}\n"
        f"Endpoint label: {label}\n"
        "Live endpoint payload (JSON):\n"
        f"{_serialize_for_llm(llm_payload)}"
    )

    try:
        summary = await llm_router.chat(selection, system_prompt, user_prompt)
        text = summary.strip()
        if not text:
            raise ValueError("empty summary")
        return text
    except Exception:
        logger.exception("Could not summarize Glances detail via LLM for key=%s", key)
        return _fallback_summary(locale, label, payload)


async def _stream_summary_to_query(
    *,
    query: object,
    locale: str,
    label: str,
    key: str,
    payload: object,
    keyboard: InlineKeyboardMarkup,
) -> str | None:
    selection = await store.get_active_model()
    system_prompt = _DETAIL_SUMMARY_SYSTEM.format(locale=locale)
    llm_payload = _prepare_llm_payload(key, payload)
    user_prompt = (
        f"Endpoint key: {key}\n"
        f"Endpoint label: {label}\n"
        "Live endpoint payload (JSON):\n"
        f"{_serialize_for_llm(llm_payload)}"
    )

    header = t("glances.detail_header", locale=locale, label=label)
    summary = ""
    last_pushed = ""
    last_edit_at = 0.0

    try:
        async for chunk in llm_router.stream_chat(selection, system_prompt, user_prompt):
            if not chunk:
                continue
            summary += chunk
            now = time.monotonic()

            candidate = summary.strip()
            if not candidate:
                continue

            if (now - last_edit_at) < _STREAM_EDIT_INTERVAL_SECONDS and candidate == last_pushed:
                continue

            edited = await _safe_edit_detail(
                query=query,
                text=f"{header}\n\n{candidate}",
                reply_markup=keyboard,
            )
            if not edited:
                return None
            last_pushed = candidate
            last_edit_at = now
    except Exception:
        logger.exception("Could not stream Glances detail summary for key=%s", key)
        return None

    final_text = summary.strip()
    if not final_text:
        return None

    if final_text != last_pushed:
        edited = await _safe_edit_detail(
            query=query,
            text=f"{header}\n\n{final_text}",
            reply_markup=keyboard,
        )
        if not edited:
            return None

    return final_text


async def _safe_edit_detail(
    *, query: object, text: str, reply_markup: InlineKeyboardMarkup
) -> bool:
    if not hasattr(query, "edit_message_text"):
        return False

    candidate = _fit_for_telegram(text)
    try:
        await query.edit_message_text(candidate, reply_markup=reply_markup)
        return True
    except BadRequest as exc:
        lower = str(exc).lower()
        if "message is not modified" in lower:
            return True
        # Benign races: user closed/deleted message while this handler was still running.
        if "message to edit not found" in lower or "message can't be edited" in lower:
            logger.info("Skipping detail edit because message is gone or not editable: %s", exc)
            return False

        if "message is too long" not in lower and "message_too_long" not in lower:
            logger.warning("Unexpected bad request editing Glances detail: %s", exc)
            return False

        short = _fit_for_telegram(candidate[:_TELEGRAM_HARD_FALLBACK_LENGTH])
        try:
            await query.edit_message_text(short, reply_markup=reply_markup)
            return True
        except (BadRequest, TimedOut, TelegramError) as short_exc:
            logger.warning("Could not edit shortened Glances detail message: %s", short_exc)
            return False
    except TimedOut as exc:
        logger.warning("Timed out editing Glances detail message: %s", exc)
        return False
    except TelegramError as exc:
        logger.warning("Telegram error editing Glances detail message: %s", exc)
        return False


async def _safe_answer(query: object) -> None:
    if not hasattr(query, "answer"):
        return
    try:
        await query.answer()
    except BadRequest as exc:
        # Can happen when callback query is already too old by the time it is processed.
        logger.warning("Could not answer callback query: %s", exc)
    except TimedOut as exc:
        logger.warning("Timed out answering callback query: %s", exc)
    except TelegramError as exc:
        logger.warning("Telegram error answering callback query: %s", exc)


async def _safe_delete_message(message: Message) -> None:
    try:
        await message.delete()
    except BadRequest as exc:
        lower = str(exc).lower()
        if "message to delete not found" in lower:
            logger.info("Glances message already deleted before close callback")
            return
        logger.warning("Could not delete Glances menu message on close: %s", exc)
    except TimedOut as exc:
        logger.warning("Timed out deleting Glances menu message on close: %s", exc)
    except TelegramError as exc:
        logger.warning("Telegram error deleting Glances menu message on close: %s", exc)


def _menu_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _label_for_key("cpu", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}cpu",
                ),
                InlineKeyboardButton(
                    _label_for_key("mem", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}mem",
                ),
                InlineKeyboardButton(
                    _label_for_key("fs", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}fs",
                ),
            ],
            [
                InlineKeyboardButton(
                    _label_for_key("load", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}load",
                ),
                InlineKeyboardButton(
                    _label_for_key("network", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}network",
                ),
                InlineKeyboardButton(
                    _label_for_key("containers", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}containers",
                ),
            ],
            [
                InlineKeyboardButton(
                    _label_for_key("processlist", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}processlist",
                ),
                InlineKeyboardButton(
                    _label_for_key("uptime", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}uptime",
                ),
            ],
            [
                InlineKeyboardButton(
                    _label_for_key("system", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}system",
                ),
                InlineKeyboardButton(
                    _label_for_key("sensors", locale),
                    callback_data=f"{_CB_SELECT_PREFIX}sensors",
                ),
            ],
            [
                InlineKeyboardButton(
                    t("glances.close_button", locale=locale),
                    callback_data=_CB_CLOSE,
                )
            ],
        ]
    )


def _detail_keyboard(locale: str, key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("glances.refresh_button", locale=locale),
                    callback_data=f"{_CB_REFRESH_PREFIX}{key}",
                ),
                InlineKeyboardButton(
                    t("glances.back_button", locale=locale),
                    callback_data=_CB_BACK,
                ),
            ],
            [
                InlineKeyboardButton(
                    t("glances.close_button", locale=locale),
                    callback_data=_CB_CLOSE,
                )
            ],
        ]
    )


async def _render_menu(update: Update, *, edit: bool) -> None:
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    text = f"{t('glances.header', locale=locale)}\n\n{t('glances.menu_hint', locale=locale)}"
    keyboard = _menu_keyboard(locale)

    if edit and update.callback_query:
        await _safe_edit_detail(
            query=update.callback_query,
            text=text,
            reply_markup=keyboard,
        )
        return

    if update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


async def _render_detail(update: Update, *, key: str) -> None:
    locale = locale_from_update(update, fallback=get_config().bot_locale)
    label = _label_for_key(key, locale)

    payload: object | None = None
    query = update.callback_query
    start = time.monotonic()
    keyboard = _detail_keyboard(locale, key)

    if query is not None:
        try:
            loading_header = t("glances.detail_header", locale=locale, label=label)
            loading_text = t("glances.loading", locale=locale)
            await _safe_edit_detail(
                query=query,
                text=f"{loading_header}\n\n{loading_text}",
                reply_markup=keyboard,
            )
        except Exception:
            logger.debug("Could not show loading state for key=%s", key, exc_info=True)

    try:
        fetch_start = time.monotonic()
        payload = await glances.get_live_endpoint_detail(key)
        fetch_elapsed = time.monotonic() - fetch_start
        summarize_start = time.monotonic()

        summary: str
        if query is not None:
            streamed = await _stream_summary_to_query(
                query=query,
                locale=locale,
                label=label,
                key=key,
                payload=payload,
                keyboard=keyboard,
            )
            if streamed is None:
                summary = await _summarize_payload(
                    locale=locale,
                    label=label,
                    key=key,
                    payload=payload,
                )
            else:
                summary = streamed
        else:
            summary = await _summarize_payload(locale=locale, label=label, key=key, payload=payload)

        summarize_elapsed = time.monotonic() - summarize_start
        logger.info(
            "Glances detail timings key=%s fetch=%.2fs summarize=%.2fs total_so_far=%.2fs",
            key,
            fetch_elapsed,
            summarize_elapsed,
            time.monotonic() - start,
        )
        text = f"{t('glances.detail_header', locale=locale, label=label)}\n\n{summary}"
    except Exception:
        if payload is None:
            logger.exception("Could not fetch live Glances detail for key=%s", key)
            text = t("glances.unavailable", locale=locale, label=label)
        else:
            text = _fallback_summary(locale, label, payload)

    if query is None:
        if update.effective_message:
            await update.effective_message.reply_text(
                _fit_for_telegram(text),
                reply_markup=keyboard,
            )
        return
    await _safe_edit_detail(query=query, text=text, reply_markup=keyboard)
    logger.info("Glances detail rendered key=%s total=%.2fs", key, time.monotonic() - start)


@restricted
async def glances_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render_menu(update, edit=False)


@restricted
async def cb_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await _safe_answer(query)
    await _render_menu(update, edit=True)


@restricted
async def cb_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await _safe_answer(query)

    key = (query.data or "").replace(_CB_SELECT_PREFIX, "", 1)
    if key not in _MENU_KEYS:
        return

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render_detail(update, key=key)


@restricted
async def cb_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await _safe_answer(query)

    key = (query.data or "").replace(_CB_REFRESH_PREFIX, "", 1)
    if key not in _MENU_KEYS:
        return

    if update.effective_chat:
        await update.effective_chat.send_action(ChatAction.TYPING)
    await _render_detail(update, key=key)


@restricted
async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await _safe_answer(query)
    await _render_menu(update, edit=True)


@restricted
async def cb_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await _safe_answer(query)

    if query.message is None:
        return
    await _safe_delete_message(cast(Message, query.message))


def register(app: Application) -> None:
    app.add_handler(CommandHandler("glances", glances_command))
    app.add_handler(CallbackQueryHandler(cb_open, pattern=f"^{_CB_OPEN}$"))
    app.add_handler(CallbackQueryHandler(cb_select, pattern=rf"^{_CB_SELECT_PREFIX}.+$"))
    app.add_handler(CallbackQueryHandler(cb_refresh, pattern=rf"^{_CB_REFRESH_PREFIX}.+$"))
    app.add_handler(CallbackQueryHandler(cb_back, pattern=f"^{_CB_BACK}$"))
    app.add_handler(CallbackQueryHandler(cb_close, pattern=f"^{_CB_CLOSE}$"))
