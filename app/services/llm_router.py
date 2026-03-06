"""Unified LLM routing across Ollama and optional cloud providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import Config, get_config
from app.core.store import split_model_selection
from app.services import ollama

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_TIMEOUT_CHAT = 120.0

# Runtime capability flags to avoid repeated unsupported-tool retries/log spam.
_anthropic_web_search_supported: bool | None = None
_deepseek_web_search_notice_logged = False


def _anthropic_web_search_tool(model: str) -> dict[str, object]:
    # Dynamic filtering version is documented for Opus/Sonnet 4.6.
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
) -> tuple[dict[str, object], bool]:
    try:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data, False
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
            return data, True
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
    chat_payload: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    }
    responses_payload: dict[str, object] = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
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

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
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


async def _chat_deepseek(model: str, system: str, user_message: str) -> str:
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
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    }

    if not _deepseek_web_search_notice_logged:
        logger.info(
            "DeepSeek web search is not enabled: Chat Completions API docs "
            "do not expose a web search parameter."
        )
        _deepseek_web_search_notice_logged = True

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
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


async def _chat_anthropic(model: str, system: str, user_message: str) -> str:
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
        "max_tokens": 512,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }
    fallback_payload: dict[str, object] | None = None

    # Best effort: try Anthropic hosted web search tool and fallback if unavailable.
    fallback_headers: dict[str, str] | None = None
    use_anthropic_web_search = _anthropic_web_search_supported is not False
    if use_anthropic_web_search:
        fallback_headers = dict(headers)
        fallback_payload = dict(payload)
        payload["tools"] = [_anthropic_web_search_tool(model)]

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
        data, used_fallback = await _post_chat_completion_with_optional_retry(
            client=client,
            url=_ANTHROPIC_URL,
            headers=headers,
            payload=payload,
            fallback_payload=fallback_payload,
            fallback_headers=fallback_headers,
            provider="Anthropic",
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
