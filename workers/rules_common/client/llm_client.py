"""
Provider-agnostic LLM client using the OpenAI-compatible Chat Completions API.

Supports any provider that speaks OpenAI's protocol — OpenRouter, Yandex Cloud,
OpenAI, Together, OpenRouter, local Ollama, etc. — selected via env vars at
runtime.

Env:
    LLM_BASE_URL   — base URL of the provider's OpenAI-compatible endpoint
                     (default: https://openrouter.ai/api/v1)
    LLM_API_KEY    — API key
    LLM_MODEL      — model name in provider's format
                     (default: google/gemini-3-flash-preview, an OpenRouter slug)

Legacy aliases (kept for backwards compat with the original Yandex setup):
    YANDEX_CLOUD_API_KEY → maps to LLM_API_KEY
    YANDEX_CLOUD_FOLDER  → composed into the model id when LLM_MODEL unset
                           and LLM_BASE_URL points at Yandex.
"""

from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL    = "google/gemini-3-flash-preview"


class LLMClient:
    """OpenAI-compatible chat-completions client."""

    def __init__(self, api_key: str, model: str,
                 base_url: str = DEFAULT_BASE_URL):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=300.0,
        )

    @classmethod
    def from_env(cls) -> "LLMClient":
        """Build a client from env vars. Prefers the generic LLM_* names,
        falls back to legacy YANDEX_CLOUD_* names."""
        base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
        api_key  = (
            os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("YANDEX_CLOUD_API_KEY")
        )
        model = os.environ.get("LLM_MODEL")

        # Legacy Yandex shape: derive model from folder when not given explicitly.
        if not model:
            folder = os.environ.get("YANDEX_CLOUD_FOLDER")
            if folder and "yandex" in base_url:
                model = f"gpt://{folder}/aliceai-llm/latest"
            else:
                model = DEFAULT_MODEL

        if not api_key:
            raise RuntimeError(
                "LLM_API_KEY is not set. Configure LLM_BASE_URL/LLM_API_KEY/LLM_MODEL "
                "or the legacy YANDEX_CLOUD_API_KEY/YANDEX_CLOUD_FOLDER."
            )

        logger.info("LLMClient: provider=%s model=%s", base_url, model)
        return cls(api_key=api_key, model=model, base_url=base_url)

    async def request(
        self,
        system_prompt: str,
        task: str,
        temperature: float = 0.0,
        max_tokens: int = 10000,
        response_format: dict | None = None,
    ) -> str:
        """Send a chat completion. response_format is forwarded only when set
        (some providers reject the parameter entirely, e.g. Yandex Cloud)."""
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": task},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=300.0,
        )
        if response_format is not None:
            kwargs["response_format"] = response_format

        completion = await self._client.chat.completions.create(**kwargs)

        result = completion.choices[0].message.content or ""
        if completion.usage:
            logger.debug(
                "tokens: prompt=%s completion=%s total=%s",
                completion.usage.prompt_tokens,
                completion.usage.completion_tokens,
                completion.usage.total_tokens,
            )
        return result
