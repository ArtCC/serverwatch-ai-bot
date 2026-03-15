"""Async client for the Ollama HTTP API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_TIMEOUT_LIST = 10.0
_TIMEOUT_CHAT = 120.0  # LLM generation can be slow on low-end hardware
_list_client: httpx.AsyncClient | None = None
_chat_client: httpx.AsyncClient | None = None
_client_guard = asyncio.Lock()


def _chat_messages(
    system: str,
    user_message: str,
    history: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        for item in history:
            role = item.get("role", "")
            content = item.get("content", "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


def _extract_model_names(data: object) -> list[str]:
    if not isinstance(data, dict):
        return []

    names: list[str] = []
    for raw in data.get("models", []):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name)
    return sorted(names)


async def list_models() -> list[str]:
    """Return a sorted list of installed Ollama model names.

    Raises httpx.HTTPError on connectivity or HTTP errors.
    """
    base_url = get_config().ollama_base_url.rstrip("/")
    client = await _get_list_client()
    resp = await client.get(f"{base_url}/api/tags")
    resp.raise_for_status()
    return _extract_model_names(resp.json())


async def pull_model(
    model_name: str,
    *,
    progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """Stream-download a model via POST /api/pull.

    Calls ``progress_callback(status, completed, total)`` for every NDJSON line
    received from Ollama.
    """
    base_url = get_config().ollama_base_url.rstrip("/")
    client = await _get_chat_client()
    async with client.stream(
        "POST",
        f"{base_url}/api/pull",
        json={"model": model_name, "stream": True},
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError(f"Pull cancelled: {model_name}")
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON chunk from Ollama pull stream")
                continue
            if not isinstance(chunk, dict):
                continue
            if err := chunk.get("error"):
                raise RuntimeError(f"Pull error: {err}")
            if progress_callback:
                await progress_callback(
                    str(chunk.get("status", "")),
                    int(chunk.get("completed", 0) or 0),
                    int(chunk.get("total", 0) or 0),
                )


async def delete_model(model_name: str) -> None:
    """Delete a local model via DELETE /api/delete."""
    base_url = get_config().ollama_base_url.rstrip("/")
    client = await _get_list_client()
    resp = await client.request(
        "DELETE",
        f"{base_url}/api/delete",
        json={"model": model_name},
    )
    resp.raise_for_status()


async def chat(
    model: str,
    system: str,
    user_message: str,
    *,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Send a chat request and return the assistant's reply text.

    Uses the /api/chat endpoint (non-streaming).
    Raises httpx.HTTPError on connectivity / HTTP errors.
    """
    base_url = get_config().ollama_base_url.rstrip("/")
    payload = {
        "model": model,
        "stream": False,
        "messages": _chat_messages(system, user_message, history),
    }
    client = await _get_chat_client()
    resp = await client.post(f"{base_url}/api/chat", json=payload)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, dict):
        raise ValueError("Ollama response is not a JSON object")
    message = data.get("message")
    if not isinstance(message, dict):
        raise ValueError("Ollama response does not include 'message'")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("Ollama response 'message.content' is missing or not a string")
    return content


async def chat_stream(
    model: str,
    system: str,
    user_message: str,
    *,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[str]:
    """Send a streaming chat request and yield incremental text chunks.

    Uses /api/chat with stream=true and yields assistant text fragments as they
    arrive. Raises httpx.HTTPError on connectivity / HTTP errors.
    """
    async for channel, chunk in chat_stream_events(
        model,
        system,
        user_message,
        history=history,
    ):
        if channel == "answer":
            yield chunk


async def chat_stream_events(
    model: str,
    system: str,
    user_message: str,
    *,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[tuple[str, str]]:
    """Send a streaming chat request and yield (channel, chunk) tuples.

    Channel values:
      - "thinking": model reasoning/thinking fragments when exposed by Ollama.
      - "answer": assistant response text fragments.

    Uses /api/chat with stream=true and raises httpx.HTTPError on connectivity
    / HTTP errors.
    """
    base_url = get_config().ollama_base_url.rstrip("/")
    payload = {
        "model": model,
        "stream": True,
        "messages": _chat_messages(system, user_message, history),
    }

    client = await _get_chat_client()
    async with client.stream("POST", f"{base_url}/api/chat", json=payload) as resp:
        resp.raise_for_status()

        async for line in resp.aiter_lines():
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON chunk from Ollama stream")
                continue

            if not isinstance(data, dict):
                continue

            thinking = data.get("thinking")
            if isinstance(thinking, str) and thinking:
                yield "thinking", thinking

            message = data.get("message")
            if isinstance(message, dict):
                model_thinking = message.get("thinking")
                if isinstance(model_thinking, str) and model_thinking:
                    yield "thinking", model_thinking

                content = message.get("content")
                if isinstance(content, str) and content:
                    yield "answer", content


async def _get_list_client() -> httpx.AsyncClient:
    global _list_client
    if _list_client is not None:
        return _list_client
    async with _client_guard:
        if _list_client is None:
            _list_client = httpx.AsyncClient(timeout=_TIMEOUT_LIST)
        return _list_client


async def _get_chat_client() -> httpx.AsyncClient:
    global _chat_client
    if _chat_client is not None:
        return _chat_client
    async with _client_guard:
        if _chat_client is None:
            _chat_client = httpx.AsyncClient(timeout=_TIMEOUT_CHAT)
        return _chat_client


async def close_clients() -> None:
    """Close shared Ollama clients on application shutdown."""
    global _list_client, _chat_client
    async with _client_guard:
        if _list_client is not None:
            await _list_client.aclose()
            _list_client = None
        if _chat_client is not None:
            await _chat_client.aclose()
            _chat_client = None
