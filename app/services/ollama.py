"""Async client for the Ollama HTTP API."""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_config

logger = logging.getLogger("serverwatch")

_TIMEOUT = 10.0


async def list_models() -> list[str]:
    """Return a sorted list of installed Ollama model names.

    Raises httpx.HTTPError on connectivity or HTTP errors.
    """
    base_url = get_config().ollama_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{base_url}/api/tags")
        resp.raise_for_status()
        data = resp.json()
        return sorted(m["name"] for m in data.get("models", []))
