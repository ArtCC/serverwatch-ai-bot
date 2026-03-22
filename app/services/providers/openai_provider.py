"""OpenAI provider implementation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx

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

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


def _extract_openai_responses_text(data: object) -> str | None:
    if not isinstance(data, dict):
        return None

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = data.get("output")
    if not isinstance(output, list):
        return None

    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "output_text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())

    if text_parts:
        return "\n".join(text_parts)
    return None


async def chat_openai(
    model: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> str:
    cfg = get_config()
    if not cfg.openai_api_key:
        raise ValueError("OPENAI_API_KEY is missing")

    headers = {
        "Authorization": f"Bearer {cfg.openai_api_key}",
        "Content-Type": "application/json",
    }
    chat_payload: dict[str, object] = {
        "model": model,
        "messages": openai_like_messages(system, user_message, history),
    }
    responses_payload: dict[str, object] = {
        "model": model,
        "input": openai_like_messages(system, user_message, history),
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
    }
    try:
        client = await get_http_client()
        resp = await client.post(
            _OPENAI_RESPONSES_URL,
            headers=headers,
            json=responses_payload,
        )
        resp.raise_for_status()
        data = resp.json()
        text = _extract_openai_responses_text(data)
        if text:
            return text
        logger.warning("OpenAI Responses API returned no text. Falling back to chat completions.")
    except httpx.HTTPStatusError:
        logger.warning(
            "OpenAI web search request failed. Retrying without web search via chat completions."
        )

    client = await get_http_client()
    resp = await client.post(_OPENAI_URL, headers=headers, json=chat_payload)
    resp.raise_for_status()
    data = resp.json()

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenAI response does not include choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("OpenAI response choice is invalid")
    message = first.get("message", {})
    if not isinstance(message, dict):
        raise ValueError("OpenAI response message is invalid")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("OpenAI response content is missing")
    return content


async def stream_openai_events(
    model: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> AsyncIterator[StreamChunk]:
    cfg = get_config()
    if not cfg.openai_api_key:
        raise ValueError("OPENAI_API_KEY is missing")

    headers = {
        "Authorization": f"Bearer {cfg.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "stream": True,
        "messages": openai_like_messages(system, user_message, history),
    }

    client = await get_http_client()
    async with client.stream("POST", _OPENAI_URL, headers=headers, json=payload) as resp:
        resp.raise_for_status()
        async for data in iter_sse_json(resp):
            thinking_chunk = extract_openai_like_reasoning_delta(data)
            if thinking_chunk:
                yield StreamChunk(channel="thinking", text=thinking_chunk)
            chunk = extract_openai_like_delta(data)
            if chunk:
                yield StreamChunk(channel="answer", text=chunk)
