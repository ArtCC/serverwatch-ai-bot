"""Unified LLM routing across Ollama and optional cloud providers."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.core.config import Config, get_config
from app.core.store import split_model_selection
from app.services import ollama
from app.services.providers.anthropic_provider import chat_anthropic, stream_anthropic_events
from app.services.providers.common import ChatHistory, close_http_client
from app.services.providers.deepseek_provider import chat_deepseek, stream_deepseek_events
from app.services.providers.openai_provider import chat_openai, stream_openai_events
from app.utils.streaming import StreamChunk

logger = logging.getLogger("serverwatch")


@dataclass(frozen=True)
class ModelOption:
    selection: str
    provider: str
    model: str


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


# ---------------------------------------------------------------------------
# Chat (non-streaming)
# ---------------------------------------------------------------------------


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
        return await chat_openai(model, system, user_message, history=history)
    if provider == "anthropic":
        return await chat_anthropic(model, system, user_message, history=history)
    if provider == "deepseek":
        return await chat_deepseek(model, system, user_message, history=history)

    raise ValueError(f"Unsupported provider: {provider}")


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


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
) -> AsyncGenerator[StreamChunk, None]:
    """Yield streaming chunks with explicit channel metadata."""
    provider, model = split_model_selection(selection)

    if provider == "ollama":
        async for chunk in _stream_with_fallback_events(
            stream_factory=lambda: _stream_ollama_events(
                model, system, user_message, history=history
            ),
            final_factory=lambda: ollama.chat(model, system, user_message, history=history),
            provider="ollama",
        ):
            yield chunk
        return
    if provider == "openai":
        async for chunk in _stream_with_fallback_events(
            stream_factory=lambda: stream_openai_events(
                model, system, user_message, history=history
            ),
            final_factory=lambda: chat_openai(model, system, user_message, history=history),
            provider="openai",
        ):
            yield chunk
        return
    if provider == "anthropic":
        async for chunk in _stream_with_fallback_events(
            stream_factory=lambda: stream_anthropic_events(
                model, system, user_message, history=history
            ),
            final_factory=lambda: chat_anthropic(model, system, user_message, history=history),
            provider="anthropic",
        ):
            yield chunk
        return
    if provider == "deepseek":
        async for chunk in _stream_with_fallback_events(
            stream_factory=lambda: stream_deepseek_events(
                model, system, user_message, history=history
            ),
            final_factory=lambda: chat_deepseek(model, system, user_message, history=history),
            provider="deepseek",
        ):
            yield chunk
        return

    raise ValueError(f"Unsupported provider: {provider}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _stream_ollama_events(
    model: str,
    system: str,
    user_message: str,
    *,
    history: ChatHistory | None = None,
) -> AsyncIterator[StreamChunk]:
    async for channel, chunk in ollama.chat_stream_events(
        model, system, user_message, history=history
    ):
        if not chunk:
            continue
        yield StreamChunk(channel=channel, text=chunk)


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


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def close_client() -> None:
    """Close the shared LLM router HTTP client on app shutdown."""
    await close_http_client()
