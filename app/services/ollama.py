"""Async client for the Ollama HTTP API."""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_TIMEOUT_LIST = 10.0
_TIMEOUT_CHAT = 120.0  # LLM generation can be slow on low-end hardware


async def list_models() -> list[str]:
    """Return a sorted list of installed Ollama model names.

    Raises httpx.HTTPError on connectivity or HTTP errors.
    """
    base_url = get_config().ollama_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=_TIMEOUT_LIST) as client:
        resp = await client.get(f"{base_url}/api/tags")
        resp.raise_for_status()
        data = resp.json()
        return sorted(m["name"] for m in data.get("models", []))


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
    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
        resp = await client.post(f"{base_url}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data["message"]["content"])
