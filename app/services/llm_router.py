"""Unified LLM routing across Ollama and optional cloud providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import Config, get_config
from app.core.store import split_model_selection
from app.services import ollama

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_TIMEOUT_CHAT = 120.0

logger = logging.getLogger("serverwatch")


@dataclass(frozen=True)
class ModelOption:
    selection: str
    provider: str
    model: str


def _is_bad_request(exc: httpx.HTTPStatusError) -> bool:
    response = exc.response
    return response is not None and response.status_code == 400


async def _post_chat_completion_with_optional_retry(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, object],
    fallback_payload: dict[str, object] | None,
    fallback_headers: dict[str, str] | None,
    provider: str,
) -> dict[str, object]:
    try:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
        raise ValueError(f"{provider} response is not a JSON object")
    except httpx.HTTPStatusError as exc:
        if fallback_payload is None or not _is_bad_request(exc):
            raise
        logger.warning(
            "%s rejected web search parameters (400). Retrying without web search.",
            provider,
        )
        retry_headers = fallback_headers or headers
        resp = await client.post(url, headers=retry_headers, json=fallback_payload)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data
        raise ValueError(f"{provider} response is not a JSON object") from exc


def configured_cloud_options(config: Config | None = None) -> list[ModelOption]:
    cfg = config or get_config()
    options: list[ModelOption] = []

    logger.debug(
        "Cloud model config present: openai=%s anthropic=%s deepseek=%s",
        bool(cfg.openai_api_key and cfg.openai_model),
        bool(cfg.anthropic_api_key and cfg.anthropic_model),
        bool(cfg.deepseek_api_key and cfg.deepseek_model),
    )

    if cfg.openai_api_key and cfg.openai_model:
        options.append(
            ModelOption(
                selection=f"openai:{cfg.openai_model}",
                provider="openai",
                model=cfg.openai_model,
            )
        )
    if cfg.anthropic_api_key and cfg.anthropic_model:
        options.append(
            ModelOption(
                selection=f"anthropic:{cfg.anthropic_model}",
                provider="anthropic",
                model=cfg.anthropic_model,
            )
        )
    if cfg.deepseek_api_key and cfg.deepseek_model:
        options.append(
            ModelOption(
                selection=f"deepseek:{cfg.deepseek_model}",
                provider="deepseek",
                model=cfg.deepseek_model,
            )
        )

    return options


def is_cloud_selection_configured(selection: str, config: Config | None = None) -> bool:
    provider, model = split_model_selection(selection)
    if provider == "ollama":
        return True

    cfg = config or get_config()
    for option in configured_cloud_options(cfg):
        if option.provider == provider and option.model == model:
            return True
    return False


async def chat(selection: str, system: str, user_message: str) -> str:
    provider, model = split_model_selection(selection)

    if provider == "ollama":
        return await ollama.chat(model, system, user_message)
    if provider == "openai":
        return await _chat_openai(model, system, user_message)
    if provider == "anthropic":
        return await _chat_anthropic(model, system, user_message)
    if provider == "deepseek":
        return await _chat_deepseek(model, system, user_message)

    raise ValueError(f"Unsupported provider: {provider}")


async def _chat_openai(model: str, system: str, user_message: str) -> str:
    cfg = get_config()
    if not cfg.openai_api_key:
        raise ValueError("OPENAI_API_KEY is missing")

    headers = {
        "Authorization": f"Bearer {cfg.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    }
    fallback_payload: dict[str, object] | None = None

    # Best effort: enable provider-side web search where supported.
    if cfg.cloud_web_search_enabled:
        fallback_payload = dict(payload)
        payload["web_search_options"] = {"search_context_size": "medium"}

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
        data = await _post_chat_completion_with_optional_retry(
            client=client,
            url=_OPENAI_URL,
            headers=headers,
            payload=payload,
            fallback_payload=fallback_payload,
            fallback_headers=None,
            provider="OpenAI",
        )

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


async def _chat_deepseek(model: str, system: str, user_message: str) -> str:
    cfg = get_config()
    if not cfg.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY is missing")

    headers = {
        "Authorization": f"Bearer {cfg.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    }
    fallback_payload: dict[str, object] | None = None

    # Best effort: DeepSeek can support web search for some models/configurations.
    if cfg.cloud_web_search_enabled:
        fallback_payload = dict(payload)
        payload["web_search"] = True

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
        data = await _post_chat_completion_with_optional_retry(
            client=client,
            url=_DEEPSEEK_URL,
            headers=headers,
            payload=payload,
            fallback_payload=fallback_payload,
            fallback_headers=None,
            provider="DeepSeek",
        )

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


async def _chat_anthropic(model: str, system: str, user_message: str) -> str:
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
        "max_tokens": 512,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }
    fallback_payload: dict[str, object] | None = None

    # Best effort: try Anthropic hosted web search tool and fallback if unavailable.
    fallback_headers: dict[str, str] | None = None
    if cfg.cloud_web_search_enabled:
        fallback_headers = dict(headers)
        headers = dict(headers)
        headers["anthropic-beta"] = "web-search-2025-03-05"
        fallback_payload = dict(payload)
        payload["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
        data = await _post_chat_completion_with_optional_retry(
            client=client,
            url=_ANTHROPIC_URL,
            headers=headers,
            payload=payload,
            fallback_payload=fallback_payload,
            fallback_headers=fallback_headers,
            provider="Anthropic",
        )

    content_list = data.get("content", [])
    if not isinstance(content_list, list) or not content_list:
        raise ValueError("Anthropic response content is missing")

    first = content_list[0]
    if not isinstance(first, dict):
        raise ValueError("Anthropic response content item is invalid")

    text = first.get("text")
    if not isinstance(text, str):
        raise ValueError("Anthropic response text is missing")
    return text
