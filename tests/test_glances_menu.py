from __future__ import annotations

import asyncio

from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, TimedOut

from app.handlers.glances_menu import _safe_answer, _safe_delete_message, _safe_edit_detail


class _FakeQuery:
    def __init__(
        self,
        *,
        edit_errors: list[Exception] | None = None,
        answer_error: Exception | None = None,
    ) -> None:
        self._edit_errors = list(edit_errors or [])
        self._answer_error = answer_error
        self.edit_calls: list[str] = []

    async def edit_message_text(self, text: str, reply_markup: InlineKeyboardMarkup) -> None:
        self.edit_calls.append(text)
        if self._edit_errors:
            raise self._edit_errors.pop(0)

    async def answer(self) -> None:
        if self._answer_error is not None:
            raise self._answer_error


class _FakeMessage:
    def __init__(self, *, delete_error: Exception | None = None) -> None:
        self._delete_error = delete_error
        self.delete_calls = 0

    async def delete(self) -> None:
        self.delete_calls += 1
        if self._delete_error is not None:
            raise self._delete_error


def test_safe_edit_detail_returns_false_on_timeout() -> None:
    query = _FakeQuery(edit_errors=[TimedOut("timeout")])
    keyboard = InlineKeyboardMarkup([])

    ok = asyncio.run(_safe_edit_detail(query=query, text="hello", reply_markup=keyboard))

    assert ok is False
    assert len(query.edit_calls) == 1


def test_safe_edit_detail_falls_back_to_short_message_on_too_long() -> None:
    query = _FakeQuery(edit_errors=[BadRequest("Message is too long")])
    keyboard = InlineKeyboardMarkup([])

    ok = asyncio.run(_safe_edit_detail(query=query, text="x" * 4500, reply_markup=keyboard))

    assert ok is True
    assert len(query.edit_calls) == 2
    assert len(query.edit_calls[1]) < len(query.edit_calls[0])


def test_safe_answer_ignores_timeout() -> None:
    query = _FakeQuery(answer_error=TimedOut("timeout"))

    asyncio.run(_safe_answer(query))


def test_safe_delete_message_ignores_not_found_bad_request() -> None:
    message = _FakeMessage(delete_error=BadRequest("Message to delete not found"))

    asyncio.run(_safe_delete_message(message))

    assert message.delete_calls == 1
