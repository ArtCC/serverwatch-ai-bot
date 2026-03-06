"""Async client for the Ollama HTTP API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_TIMEOUT_LIST = 10.0
_TIMEOUT_CHAT = 120.0  # LLM generation can be slow on low-end hardware
_list_client: httpx.AsyncClient | None = None
_chat_client: httpx.AsyncClient | None = None
_client_guard = asyncio.Lock()


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


async def chat(model: str, system: str, user_message: str) -> str:
    """Send a chat request and return the assistant's reply text.

    Uses the /api/chat endpoint (non-streaming).
    Raises httpx.HTTPError on connectivity / HTTP errors.
    """
    base_url = get_config().ollama_base_url.rstrip("/")
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
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


async def chat_stream(model: str, system: str, user_message: str) -> AsyncIterator[str]:
    """Send a streaming chat request and yield incremental text chunks.

    Uses /api/chat with stream=true and yields message.content fragments as they
    arrive. Raises httpx.HTTPError on connectivity / HTTP errors.
    """
    base_url = get_config().ollama_base_url.rstrip("/")
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
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

            message = data.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content:
                    yield content


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
