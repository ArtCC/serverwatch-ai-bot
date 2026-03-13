"""Unified LLM routing across Ollama and optional cloud providers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
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
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


@dataclass(frozen=True)
class ModelOption:
    selection: str
    provider: str
    model: str


@dataclass(frozen=True)
class StreamChunk:
    channel: str
    text: str


ChatHistory = list[dict[str, str]]


def _sanitize_history(history: ChatHistory | None) -> ChatHistory:
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


def _openai_like_messages(
    system: str,
    user_message: str,
    history: ChatHistory | None,
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system}]
    messages.extend(_sanitize_history(history))
    messages.append({"role": "user", "content": user_message})
    return messages


def _anthropic_messages(user_message: str, history: ChatHistory | None) -> list[dict[str, str]]:
    messages = _sanitize_history(history)
    messages.append({"role": "user", "content": user_message})
    return messages


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


async def chat(
    selection: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> str:
    provider, model = split_model_selection(selection)

    if provider == "ollama":
        return await ollama.chat(model, system, user_message, history=history)
    if provider == "openai":
        return await _chat_openai(model, system, user_message, history=history)
    if provider == "anthropic":
        return await _chat_anthropic(model, system, user_message, history=history)
    if provider == "deepseek":
        return await _chat_deepseek(model, system, user_message, history=history)

    raise ValueError(f"Unsupported provider: {provider}")


async def stream_chat(
    selection: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> AsyncIterator[str]:
    """Yield chat output chunks when provider supports streaming."""
    async for chunk in stream_chat_events(
        selection,
        system,
        user_message,
        history=history,
    ):
        if chunk.channel == "answer":
            yield chunk.text


async def stream_chat_events(
    selection: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> AsyncIterator[StreamChunk]:
    """Yield streaming chunks with explicit channel metadata.

    Returned channels are:
      - "thinking": intermediate model reasoning blocks (if provider exposes them)
      - "answer": final response text stream
    """
    provider, model = split_model_selection(selection)

    if provider == "ollama":
        async for chunk in _stream_with_fallback_events(
            stream_factory=lambda: _stream_ollama_events(
                model,
                system,
                user_message,
                history=history,
            ),
            final_factory=lambda: ollama.chat(model, system, user_message, history=history),
            provider="ollama",
        ):
            yield chunk
        return
    if provider == "openai":
        async for chunk in _stream_with_fallback_events(
            stream_factory=lambda: _stream_openai_events(
                model,
                system,
                user_message,
                history=history,
            ),
            final_factory=lambda: _chat_openai(model, system, user_message, history=history),
            provider="openai",
        ):
            yield chunk
        return
    if provider == "anthropic":
        async for chunk in _stream_with_fallback_events(
            stream_factory=lambda: _stream_anthropic_events(
                model,
                system,
                user_message,
                history=history,
            ),
            final_factory=lambda: _chat_anthropic(model, system, user_message, history=history),
            provider="anthropic",
        ):
            yield chunk
        return
    if provider == "deepseek":
        async for chunk in _stream_with_fallback_events(
            stream_factory=lambda: _stream_deepseek_events(
                model,
                system,
                user_message,
                history=history,
            ),
            final_factory=lambda: _chat_deepseek(model, system, user_message, history=history),
            provider="deepseek",
        ):
            yield chunk
        return

    raise ValueError(f"Unsupported provider: {provider}")


async def _stream_with_fallback(
    *,
    stream_factory: Callable[[], AsyncIterator[str]],
    final_factory: Callable[[], Awaitable[str]],
    provider: str,
) -> AsyncIterator[str]:
    """Try streaming first and fallback to a full response if nothing was emitted."""
    emitted_any = False
    try:
        async for chunk in stream_factory():
            emitted_any = True
            yield chunk
    except Exception:
        if emitted_any:
            raise
        logger.warning(
            "%s streaming failed before first chunk. Falling back to non-stream.", provider
        )

    if emitted_any:
        return

    full = await final_factory()
    if isinstance(full, str) and full:
        yield full


async def _stream_with_fallback_events(
    *,
    stream_factory: Callable[[], AsyncIterator[StreamChunk]],
    final_factory: Callable[[], Awaitable[str]],
    provider: str,
) -> AsyncIterator[StreamChunk]:
    """Try structured streaming first and fallback to a full answer chunk."""
    emitted_any = False
    try:
        async for chunk in stream_factory():
            emitted_any = True
            yield chunk
    except Exception:
        if emitted_any:
            raise
        logger.warning(
            "%s streaming failed before first chunk. Falling back to non-stream.", provider
        )

    if emitted_any:
        return

    full = await final_factory()
    if isinstance(full, str) and full:
        yield StreamChunk(channel="answer", text=full)


async def _iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, object]]:
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


def _extract_openai_like_delta(data: dict[str, object]) -> str:
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


def _extract_openai_like_reasoning_delta(data: dict[str, object]) -> str:
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


async def _stream_ollama_events(
    model: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> AsyncIterator[StreamChunk]:
    async for channel, chunk in ollama.chat_stream_events(
        model,
        system,
        user_message,
        history=history,
    ):
        if not chunk:
            continue
        yield StreamChunk(channel=channel, text=chunk)


async def _stream_openai_events(
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
        "messages": _openai_like_messages(system, user_message, history),
    }

    client = await _get_http_client()
    async with client.stream("POST", _OPENAI_URL, headers=headers, json=payload) as resp:
        resp.raise_for_status()
        async for data in _iter_sse_json(resp):
            thinking_chunk = _extract_openai_like_reasoning_delta(data)
            if thinking_chunk:
                yield StreamChunk(channel="thinking", text=thinking_chunk)
            chunk = _extract_openai_like_delta(data)
            if chunk:
                yield StreamChunk(channel="answer", text=chunk)


async def _stream_deepseek_events(
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
        "messages": _openai_like_messages(system, user_message, history),
    }

    client = await _get_http_client()
    async with client.stream("POST", _DEEPSEEK_URL, headers=headers, json=payload) as resp:
        resp.raise_for_status()
        async for data in _iter_sse_json(resp):
            thinking_chunk = _extract_openai_like_reasoning_delta(data)
            if thinking_chunk:
                yield StreamChunk(channel="thinking", text=thinking_chunk)
            chunk = _extract_openai_like_delta(data)
            if chunk:
                yield StreamChunk(channel="answer", text=chunk)


async def _stream_anthropic_events(
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
        "max_tokens": 512,
        "stream": True,
        "system": system,
        "messages": _anthropic_messages(user_message, history),
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
        if not use_tool or not _is_bad_request(exc):
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
    client = await _get_http_client()
    async with client.stream("POST", _ANTHROPIC_URL, headers=headers, json=payload) as resp:
        resp.raise_for_status()
        async for data in _iter_sse_json(resp):
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


async def _chat_openai(
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
        "messages": _openai_like_messages(system, user_message, history),
    }
    responses_payload: dict[str, object] = {
        "model": model,
        "input": _openai_like_messages(system, user_message, history),
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
    }
    try:
        client = await _get_http_client()
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

    client = await _get_http_client()
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


async def _chat_deepseek(
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
        "messages": _openai_like_messages(system, user_message, history),
    }

    if not _deepseek_web_search_notice_logged:
        logger.info(
            "DeepSeek web search is not enabled: Chat Completions API docs "
            "do not expose a web search parameter."
        )
        _deepseek_web_search_notice_logged = True

    client = await _get_http_client()
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


async def _chat_anthropic(
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
        "max_tokens": 512,
        "system": system,
        "messages": _anthropic_messages(user_message, history),
    }
    fallback_payload: dict[str, object] | None = None

    # Best effort: try Anthropic hosted web search tool and fallback if unavailable.
    fallback_headers: dict[str, str] | None = None
    use_anthropic_web_search = _anthropic_web_search_supported is not False
    if use_anthropic_web_search:
        fallback_headers = dict(headers)
        fallback_payload = dict(payload)
        payload["tools"] = [_anthropic_web_search_tool(model)]

    client = await _get_http_client()
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


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is not None:
        return _http_client

    async with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.AsyncClient(timeout=_TIMEOUT_CHAT)
        return _http_client


async def close_client() -> None:
    """Close the shared LLM router HTTP client on app shutdown."""
    global _http_client
    async with _http_client_lock:
        if _http_client is None:
            return
        await _http_client.aclose()
        _http_client = None
