"""Tests for _trim_history_window and sanitize_history."""

from app.core.store import _trim_history_window
from app.services.providers.common import sanitize_history


def test_trim_empty_returns_empty() -> None:
    assert _trim_history_window([], max_messages=10, max_chars=5000) == []


def test_trim_respects_max_messages() -> None:
    messages = [("user", f"msg{i}") for i in range(10)]
    result = _trim_history_window(messages, max_messages=3, max_chars=50000)
    assert len(result) == 3
    assert result[0]["content"] == "msg7"


def test_trim_respects_max_chars() -> None:
    messages = [
        ("user", "short"),
        ("assistant", "a" * 100),
        ("user", "final"),
    ]
    result = _trim_history_window(messages, max_messages=10, max_chars=20)
    assert len(result) >= 1
    assert result[-1]["content"] == "final"


def test_trim_clips_single_oversized_message() -> None:
    messages = [("user", "x" * 200)]
    result = _trim_history_window(messages, max_messages=10, max_chars=50)
    assert len(result) == 1
    assert len(result[0]["content"]) == 50


def test_trim_zero_limits_returns_empty() -> None:
    messages = [("user", "hello")]
    assert _trim_history_window(messages, max_messages=0, max_chars=1000) == []
    assert _trim_history_window(messages, max_messages=10, max_chars=0) == []


def test_sanitize_history_filters_invalid_roles() -> None:
    history = [
        {"role": "user", "content": "good"},
        {"role": "system", "content": "bad — should be filtered"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "  "},
    ]
    result = sanitize_history(history)
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"


def test_sanitize_history_none_returns_empty() -> None:
    assert sanitize_history(None) == []
