"""
LLM Client - Клиент для OpenRouter/LangChain

Поддержка различных LLM моделей через OpenRouter API.
"""

import logging
import os
import hashlib
import math
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """Клиент для работы с LLM через OpenRouter."""

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=300.0,
        )
        # Промпты теперь в core/prompts
        self._prompts_dir = Path(__file__).parent.parent / "core"

    @classmethod
    def from_env(cls) -> "LLMClient":
        """Создает клиент из переменных окружения."""
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("Нужно выставить OPENROUTER_API_KEY в окружении")
        return cls(api_key=api_key)

    def load_prompt(self, name: str) -> str:
        """Загружает промпт из файла."""
        prompt_path = self._prompts_dir / "prompts" / f"{name}.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Промпт не найден: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8")

    async def request(
        self,
        system_prompt: str,
        task: str,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4000,
        response_format: dict | None = None,
    ) -> str:
        """
        Отправляет запрос к LLM.

        Args:
            system_prompt: Системный промпт
            task: Задача для выполнения
            model: Модель для использования
            temperature: Температура генерации
            max_tokens: Максимальное количество токенов

        Returns:
            str: Ответ от LLM
        """
        try:
            model_name = model or os.environ.get("OPENROUTER_MODEL") or "anthropic/claude-3-haiku"
            response = await self._client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": task},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Ошибка при запросе к LLM: {e}")
            raise

    @staticmethod
    def _mock_embedding(text: str, dim: int = 256) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values = [0.0] * dim
        for i in range(dim):
            b = seed[i % len(seed)]
            values[i] = (b / 255.0) - 0.5
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    async def embed(
        self,
        texts: list[str],
        model: Optional[str] = None,
    ) -> list[list[float]]:
        model_name = model or os.environ.get("OPENROUTER_EMBEDDING_MODEL") or "mock"
        if model_name == "mock":
            return [self._mock_embedding(t) for t in texts]

        response = await self._client.embeddings.create(
            model=model_name,
            input=texts,
        )
        return [item.embedding for item in response.data]
