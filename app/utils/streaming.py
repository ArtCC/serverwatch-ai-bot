"""Unified LLM → Telegram streaming helper.

Provides :func:`stream_to_telegram` which consumes an async generator of
:class:`StreamChunk` objects and progressively edits a Telegram message,
giving the user a smooth *letter-by-letter* experience.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from telegram import InlineKeyboardMarkup, Message
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TimedOut

logger = logging.getLogger("serverwatch")

_TELEGRAM_MAX_TEXT_LENGTH = 4096
DEFAULT_EDIT_INTERVAL = 0.3
DEFAULT_TYPING_INTERVAL = 4.0


@dataclass(frozen=True)
class StreamChunk:
    """One piece of streamed LLM output."""

    channel: str  # "thinking" | "answer"
    text: str


@dataclass
class StreamResult:
    """Accumulated output after a streaming operation finishes."""

    thinking: str
    answer: str
    cancelled: bool
    last_pushed_text: str


def truncate_for_telegram(text: str) -> str:
    """Truncate *text* to fit Telegram's message-length limit."""
    if len(text) <= _TELEGRAM_MAX_TEXT_LENGTH:
        return text
    return text[: _TELEGRAM_MAX_TEXT_LENGTH - 1] + "\u2026"


# ---------------------------------------------------------------------------
# Public streaming helper
# ---------------------------------------------------------------------------


async def stream_to_telegram(
    *,
    stream: AsyncGenerator[StreamChunk, None],
    placeholder: Message,
    chat: object | None = None,
    cancel_event: asyncio.Event | None = None,
    stream_keyboard: InlineKeyboardMarkup | None = None,
    thinking_label: str = "",
    prefix: str = "",
    edit_interval: float = DEFAULT_EDIT_INTERVAL,
    typing_interval: float = DEFAULT_TYPING_INTERVAL,
) -> StreamResult:
    """Stream LLM chunks into a Telegram message with throttled edits.

    The *stream* is **always** closed (``aclose``) when this function
    returns — the caller does not need to handle cleanup.

    Parameters
    ----------
    stream:
        Async generator of :class:`StreamChunk` from the LLM router.
    placeholder:
        Message to edit with progressive updates.
    chat:
        Effective chat for periodic *typing…* actions (optional).
    cancel_event:
        Triggers immediate cancellation when set.
    stream_keyboard:
        Inline keyboard attached to every mid-stream edit.
    thinking_label:
        Text shown while only *thinking* chunks have arrived.
    prefix:
        Static header prepended to every edit (e.g. section title).
    edit_interval:
        Minimum seconds between consecutive edits (lower = smoother).
    typing_interval:
        Seconds between *typing…* actions.
    """
    thinking = ""
    answer = ""
    last_pushed = ""
    last_edit_at = 0.0
    last_typing_at = time.monotonic()
    edits_ok = True
    cancelled = False

    async def _process(chunk: StreamChunk) -> None:  # noqa: C901
        nonlocal thinking, answer, last_pushed, last_edit_at, last_typing_at, edits_ok

        if not chunk.text:
            return

        if chunk.channel == "thinking":
            thinking += chunk.text
        else:
            answer += chunk.text

        # ---- build display text ----
        if answer:
            display = answer
        elif thinking and thinking_label:
            display = f"{thinking_label}\n\n{thinking}"
        else:
            return

        if prefix:
            display = f"{prefix}\n\n{display}"
        display = truncate_for_telegram(display)

        now = time.monotonic()

        # Periodic typing action.
        if chat is not None and (now - last_typing_at) >= typing_interval:
            try:
                await chat.send_action(ChatAction.TYPING)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            last_typing_at = now

        # Throttled edit.
        if edits_ok and display != last_pushed and (now - last_edit_at) >= edit_interval:
            ok = await _safe_edit(placeholder, display, reply_markup=stream_keyboard)
            if ok:
                last_pushed = display
                last_edit_at = now
            else:
                edits_ok = False

    try:
        if cancel_event is not None:
            # Race each chunk against the cancel event for instant cancellation.
            while True:
                next_task: asyncio.Task[StreamChunk] = asyncio.create_task(_anext_chunk(stream))
                cancel_task: asyncio.Task[bool] = asyncio.create_task(cancel_event.wait())

                done, _ = await asyncio.wait(
                    {next_task, cancel_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if cancel_task in done and cancel_event.is_set():
                    cancelled = True
                    next_task.cancel()
                    await asyncio.gather(next_task, return_exceptions=True)
                    break

                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)

                try:
                    chunk = await next_task
                except StopAsyncIteration:
                    break

                await _process(chunk)
        else:
            # Simple loop — no cancellation support needed.
            async for chunk in stream:
                await _process(chunk)
    finally:
        await stream.aclose()

    return StreamResult(
        thinking=thinking,
        answer=answer,
        cancelled=cancelled,
        last_pushed_text=last_pushed,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _anext_chunk(stream: AsyncGenerator[StreamChunk, None]) -> StreamChunk:
    """Wrapper so ``anext`` can be fed to ``asyncio.create_task``."""
    return await anext(stream)


async def _safe_edit(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Best-effort message edit; returns ``False`` on unrecoverable failure."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return True
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return True
        logger.warning("Could not edit streaming message: %s", exc)
    except (TimedOut, NetworkError):
        logger.warning("Telegram timeout/network error during streaming edit")
    except Exception:
        logger.exception("Unexpected error during streaming edit")
    return False
