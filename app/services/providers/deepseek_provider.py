"""DeepSeek provider implementation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.core.config import get_config
from app.services.providers.common import (
    ChatHistory,
    extract_openai_like_delta,
    extract_openai_like_reasoning_delta,
    get_http_client,
    iter_sse_json,
    openai_like_messages,
)
from app.utils.streaming import StreamChunk

logger = logging.getLogger("serverwatch")

_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

_deepseek_web_search_notice_logged = False


async def chat_deepseek(
    model: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> str:
    global _deepseek_web_search_notice_logged

    cfg = get_config()
    if not cfg.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY is missing")

    headers = {
        "Authorization": f"Bearer {cfg.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {
        "model": model,
        "messages": openai_like_messages(system, user_message, history),
    }

    if not _deepseek_web_search_notice_logged:
        logger.info(
            "DeepSeek web search is not enabled: Chat Completions API docs "
            "do not expose a web search parameter."
        )
        _deepseek_web_search_notice_logged = True

    client = await get_http_client()
    resp = await client.post(_DEEPSEEK_URL, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("DeepSeek response does not include choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("DeepSeek response choice is invalid")
    message = first.get("message", {})
    if not isinstance(message, dict):
        raise ValueError("DeepSeek response message is invalid")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("DeepSeek response content is missing")
    return content


async def stream_deepseek_events(
    model: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> AsyncIterator[StreamChunk]:
    cfg = get_config()
    if not cfg.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY is missing")

    headers = {
        "Authorization": f"Bearer {cfg.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "stream": True,
        "messages": openai_like_messages(system, user_message, history),
    }

    client = await get_http_client()
    async with client.stream("POST", _DEEPSEEK_URL, headers=headers, json=payload) as resp:
        resp.raise_for_status()
        async for data in iter_sse_json(resp):
            thinking_chunk = extract_openai_like_reasoning_delta(data)
            if thinking_chunk:
                yield StreamChunk(channel="thinking", text=thinking_chunk)
            chunk = extract_openai_like_delta(data)
            if chunk:
                yield StreamChunk(channel="answer", text=chunk)
