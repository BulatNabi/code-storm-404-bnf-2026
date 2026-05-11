"""
Общий LLM клиент для работы с Yandex Qwen.
"""

import logging
import os
from pathlib import Path
import dotenv

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """Клиент для работы с LLM (Yandex Qwen)."""

    def __init__(self, api_key: str, folder: str):
        self.api_key = api_key
        self.folder = folder
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://llm.api.cloud.yandex.net/v1",
            timeout=300.0,
        )
        self._prompts_dir = Path(__file__).parent.parent.parent / "chunker" / "prompts"

    @classmethod
    def from_env(cls) -> "LLMClient":
        """Создает клиент из переменных окружения."""
        api_key = os.environ.get("YANDEX_CLOUD_API_KEY")
        folder = os.environ.get("YANDEX_CLOUD_FOLDER")

        if not api_key or not folder:
            raise RuntimeError(
                "Нужно выставить YANDEX_CLOUD_API_KEY и "
                "YANDEX_CLOUD_FOLDER в окружении"
            )

        return cls(api_key=api_key, folder=folder)

    def load_prompt(self, name: str) -> str:
        """Загружает промпт из файла (по умолчанию chunker/prompts)."""
        prompt_path = self._prompts_dir / f"{name}.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Промпт не найден: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8")

    async def request(
        self,
        system_prompt: str,
        task: str,
        temperature: float = 0.0,
        max_tokens: int = 10000,
        response_format: dict | None = None,
    ) -> str:
        """Отправляет запрос к LLM."""
        model_name = f"gpt://{self.folder}/qwen3-235b-a22b-fp8/latest"

        logger.debug("LLMClient.request: отправка запроса...")
        logger.debug(f"Длина system_prompt: {len(system_prompt)} символов")
        logger.debug(f"Длина task: {len(task)} символов")

        completion = await self._client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=300.0,
            response_format=response_format,
        )

        result = completion.choices[0].message.content
        logger.debug(f"Ответ получен, длина: {len(result)} символов")
        logger.debug(
            f"Токены: prompt={completion.usage.prompt_tokens}, "
            f"completion={completion.usage.completion_tokens}, "
            f"total={completion.usage.total_tokens}"
        )

        return result

