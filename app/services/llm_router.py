"""Unified LLM routing across Ollama and optional cloud providers."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import Config, get_config
from app.core.store import split_model_selection
from app.services import ollama

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_TIMEOUT_CHAT = 120.0


@dataclass(frozen=True)
class ModelOption:
    selection: str
    provider: str
    model: str


def configured_cloud_options(config: Config | None = None) -> list[ModelOption]:
    cfg = config or get_config()
    options: list[ModelOption] = []

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
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
        resp = await client.post(_OPENAI_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError("OpenAI response does not include choices")
    message = choices[0].get("message", {})
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
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
        resp = await client.post(_DEEPSEEK_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError("DeepSeek response does not include choices")
    message = choices[0].get("message", {})
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
    payload = {
        "model": model,
        "max_tokens": 512,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT_CHAT) as client:
        resp = await client.post(_ANTHROPIC_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

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
