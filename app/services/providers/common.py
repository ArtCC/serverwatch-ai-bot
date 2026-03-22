"""Shared utilities for cloud LLM providers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx

logger = logging.getLogger("serverwatch")

_TIMEOUT_CHAT = 120.0
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()

ChatHistory = list[dict[str, str]]


def sanitize_history(history: ChatHistory | None) -> ChatHistory:
    if not history:
        return []

    sanitized: ChatHistory = []
    for item in history:
        role = item.get("role", "")
        content = item.get("content", "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        sanitized.append({"role": role, "content": content})
    return sanitized


def openai_like_messages(
    system: str,
    user_message: str,
    history: ChatHistory | None,
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system}]
    messages.extend(sanitize_history(history))
    messages.append({"role": "user", "content": user_message})
    return messages


def anthropic_messages(user_message: str, history: ChatHistory | None) -> list[dict[str, str]]:
    messages = sanitize_history(history)
    messages.append({"role": "user", "content": user_message})
    return messages


def is_bad_request(exc: httpx.HTTPStatusError) -> bool:
    response = exc.response
    return response is not None and response.status_code == 400


async def iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, object]]:
    """Parse SSE data lines containing JSON payloads."""
    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue

        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("Skipping invalid JSON SSE payload")
            continue

        if isinstance(data, dict):
            yield data


def extract_openai_like_delta(data: dict[str, object]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    if not isinstance(first, dict):
        return ""

    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""

    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
        return "".join(text_parts)
    return ""


def extract_openai_like_reasoning_delta(data: dict[str, object]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    if not isinstance(first, dict):
        return ""

    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""

    reasoning_content = delta.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        return reasoning_content

    thinking = delta.get("thinking")
    if isinstance(thinking, str) and thinking:
        return thinking

    reasoning = delta.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    if isinstance(reasoning, dict):
        text = reasoning.get("text")
        if isinstance(text, str) and text:
            return text

        content = reasoning.get("content")
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_text = block.get("text")
                if isinstance(block_text, str) and block_text:
                    parts.append(block_text)
            return "".join(parts)

    return ""


async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is not None:
        return _http_client

    async with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.AsyncClient(timeout=_TIMEOUT_CHAT)
        return _http_client


async def close_http_client() -> None:
    """Close the shared LLM router HTTP client on app shutdown."""
    global _http_client
    async with _http_client_lock:
        if _http_client is None:
            return
        await _http_client.aclose()
        _http_client = None
