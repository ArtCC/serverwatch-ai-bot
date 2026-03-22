"""Anthropic provider implementation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

from app.core.config import get_config
from app.services.providers.common import (
    ChatHistory,
    anthropic_messages,
    get_http_client,
    is_bad_request,
    iter_sse_json,
)
from app.utils.streaming import StreamChunk

logger = logging.getLogger("serverwatch")

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Runtime capability flag to avoid repeated unsupported-tool retries/log spam.
_anthropic_web_search_supported: bool | None = None


def _anthropic_web_search_tool(model: str) -> dict[str, object]:
    if model.startswith("claude-opus-4-6") or model.startswith("claude-sonnet-4-6"):
        return {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 5,
        }
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 5,
    }


async def _post_chat_completion_with_optional_retry(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, object],
    fallback_payload: dict[str, object] | None,
    fallback_headers: dict[str, str] | None,
) -> tuple[dict[str, object], bool]:
    try:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data, False
        raise ValueError("Anthropic response is not a JSON object")
    except httpx.HTTPStatusError as exc:
        if fallback_payload is None or not is_bad_request(exc):
            raise
        logger.warning(
            "Anthropic rejected web search parameters (400). Retrying without web search.",
        )
        retry_headers = fallback_headers or headers
        resp = await client.post(url, headers=retry_headers, json=fallback_payload)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data, True
        raise ValueError("Anthropic response is not a JSON object") from exc


async def chat_anthropic(
    model: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> str:
    global _anthropic_web_search_supported

    cfg = get_config()
    if not cfg.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is missing")

    headers = {
        "x-api-key": cfg.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload: dict[str, object] = {
        "model": model,
        "max_tokens": cfg.anthropic_max_tokens,
        "system": system,
        "messages": anthropic_messages(user_message, history),
    }
    fallback_payload: dict[str, object] | None = None

    fallback_headers: dict[str, str] | None = None
    use_anthropic_web_search = _anthropic_web_search_supported is not False
    if use_anthropic_web_search:
        fallback_headers = dict(headers)
        fallback_payload = dict(payload)
        payload["tools"] = [_anthropic_web_search_tool(model)]

    client = await get_http_client()
    data, used_fallback = await _post_chat_completion_with_optional_retry(
        client=client,
        url=_ANTHROPIC_URL,
        headers=headers,
        payload=payload,
        fallback_payload=fallback_payload,
        fallback_headers=fallback_headers,
    )

    if use_anthropic_web_search and used_fallback:
        _anthropic_web_search_supported = False
        logger.warning(
            "Anthropic web search disabled for this runtime after 400 response. "
            "Using regular Messages API for next requests."
        )
    elif use_anthropic_web_search and _anthropic_web_search_supported is None:
        _anthropic_web_search_supported = True

    content_list = data.get("content", [])
    if not isinstance(content_list, list) or not content_list:
        raise ValueError("Anthropic response content is missing")

    text_parts: list[str] = []
    for block in content_list:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())

    if text_parts:
        return "\n".join(text_parts)

    stop_reason = data.get("stop_reason")
    if isinstance(stop_reason, str):
        raise ValueError(f"Anthropic response text is missing (stop_reason={stop_reason})")
    raise ValueError("Anthropic response text is missing")


async def stream_anthropic_events(
    model: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> AsyncIterator[StreamChunk]:
    global _anthropic_web_search_supported

    cfg = get_config()
    if not cfg.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is missing")

    headers = {
        "x-api-key": cfg.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    base_payload: dict[str, object] = {
        "model": model,
        "max_tokens": cfg.anthropic_max_tokens,
        "stream": True,
        "system": system,
        "messages": anthropic_messages(user_message, history),
    }

    use_tool = _anthropic_web_search_supported is not False
    payload = dict(base_payload)
    if use_tool:
        payload["tools"] = [_anthropic_web_search_tool(model)]

    try:
        async for chunk in _stream_anthropic_once_events(headers=headers, payload=payload):
            yield chunk
        if use_tool and _anthropic_web_search_supported is None:
            _anthropic_web_search_supported = True
        return
    except httpx.HTTPStatusError as exc:
        if not use_tool or not is_bad_request(exc):
            raise
        _anthropic_web_search_supported = False
        logger.warning(
            "Anthropic rejected web search parameters for streaming (400). "
            "Retrying without web search."
        )

    async for chunk in _stream_anthropic_once_events(headers=headers, payload=base_payload):
        yield chunk


async def _stream_anthropic_once_events(
    *, headers: dict[str, str], payload: dict[str, object]
) -> AsyncIterator[StreamChunk]:
    client = await get_http_client()
    async with client.stream("POST", _ANTHROPIC_URL, headers=headers, json=payload) as resp:
        resp.raise_for_status()
        async for data in iter_sse_json(resp):
            event_type = data.get("type")
            if event_type == "content_block_delta":
                delta = data.get("delta")
                if not isinstance(delta, dict):
                    continue
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        yield StreamChunk(channel="answer", text=text)
                elif delta_type == "thinking_delta":
                    thinking = delta.get("thinking")
                    if isinstance(thinking, str) and thinking:
                        yield StreamChunk(channel="thinking", text=thinking)
