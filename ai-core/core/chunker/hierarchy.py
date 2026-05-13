"""
LLM восстановление иерархии заголовков.
"""

import json
import logging

from .llm_client import LLMClient
from .models import DEFAULT_MAX_DEPTH

logger = logging.getLogger(__name__)


class HierarchyCorrector:
    """LLM коррекция иерархии заголовков."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def correct(
        self,
        headers: list[dict],
        max_depth: int = DEFAULT_MAX_DEPTH
    ) -> list[dict]:
        """
        Отдаёт LLM список заголовков и просит исправить иерархию.
        Возвращает структурированный JSON с сохранением line_idx.

        Args:
            headers: [{line_idx, level, title, source}, ...]
            max_depth: Максимальная глубина вложенности

        Returns:
            Список словарей: [{line_idx, level, title}, ...]
        """
        logger.debug("HierarchyCorrector.correct: начало (STRUCTURED OUTPUT)")
        logger.debug("Входных заголовков: %d", len(headers))
        logger.debug("max_depth: %d", max_depth)

        if max_depth < 1:
            raise ValueError(
                f"max_depth должен быть >= 1, получено: {max_depth}"
            )

        system_prompt = self.llm.load_prompt("hierarchy_structured")
        logger.debug(
            "Системный промпт загружен, длина: %d символов", len(system_prompt)
        )

        task = (
            "Вот список заголовков (JSON):\n\n"
            + json.dumps(headers, ensure_ascii=False, indent=2)
            + f"\n\nОГРАНИЧЕНИЕ: максимальная глубина = {max_depth} уровня. "
            + f"Заголовки глубже {max_depth} уровня НЕ ВКЛЮЧАЙ в ответ."
        )
        logger.debug("Задача сформирована, длина: %d символов", len(task))

        logger.debug(
            "ДАННЫЕ ДЛЯ ПОСТРОЕНИЯ ИЕРАРХИИ: %d заголовков", len(headers)
        )
        for i, h in enumerate(headers[:5]):
            logger.debug(
                "  [%d] line_idx=%d, level=%d, title=%s...",
                i+1, h['line_idx'], h['level'], h['title'][:50]
            )

        logger.debug("Отправка запроса к LLM...")
        result = await self.llm.request(system_prompt, task)
        logger.debug("Ответ получен, длина: %d символов", len(result))
        logger.debug("Ответ LLM: %s...", result[:500])

        raw_str = result.strip()

        if raw_str.startswith("```"):
            raw_str = raw_str.strip("`")
            if raw_str.startswith("json"):
                raw_str = raw_str[4:].strip()

        try:
            data = json.loads(raw_str)
            parsed_headers = data.get("headers", [])
            logger.debug("JSON распарсен, заголовков: %d", len(parsed_headers))

            input_line_idxs = {h["line_idx"] for h in headers}
            output_line_idxs = {h["line_idx"] for h in parsed_headers}

            added = output_line_idxs - input_line_idxs
            if added:
                logger.warning(
                    "LLM добавила заголовки с новыми line_idx: %s", added
                )

            filtered = [
                h for h in parsed_headers if h.get("level", 1) <= max_depth
            ]
            if len(filtered) < len(parsed_headers):
                logger.debug(
                    "Отфильтровано по глубине: %d -> %d",
                    len(parsed_headers), len(filtered)
                )

            filtered.sort(key=lambda h: h.get("line_idx", 0))

            return filtered

        except json.JSONDecodeError as e:
            logger.error("Не удалось распарсить JSON: %s", e)
            logger.error("Ответ LLM: %s...", raw_str[:500])

            logger.warning("FALLBACK: Возвращаем исходные заголовки")
            return [
                {
                    "line_idx": h["line_idx"],
                    "level": h["level"],
                    "title": h["title"]
                }
                for h in headers
                if h.get("level", 1) <= max_depth
            ]
