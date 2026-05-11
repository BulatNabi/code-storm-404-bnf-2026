"""
LLM классификация кандидатов в заголовки.
"""

import json
import logging
from typing import List, Optional

from .llm_client import LLMClient
from .models import LLMHeaderCandidate, LLMHeaderDecision
from .headers import HeaderFinder

logger = logging.getLogger(__name__)


def build_llm_candidates(
    lines: list[str],
    skip_lines: Optional[set[int]] = None
) -> List[LLMHeaderCandidate]:
    """
    Формирует кандидатов для LLM на основе эвристик.
    """
    finder = HeaderFinder()
    explicit = finder.find_explicit(lines, skip_lines)
    raw_candidates = finder.find_candidates(lines, explicit, skip_lines)

    result: List[LLMHeaderCandidate] = []
    for idx, text in raw_candidates:
        start_before = max(0, idx - 2)
        end_after = min(len(lines), idx + 3)

        before = "\n".join(lines[start_before:idx])
        after = "\n".join(lines[idx + 1:end_after])

        result.append(
            LLMHeaderCandidate(
                line_idx=idx,
                text=text,
                before=before,
                after=after,
            )
        )
    return result


class HeaderClassifier:
    """LLM классификация кандидатов в заголовки."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def classify(
        self,
        candidates: List[LLMHeaderCandidate],
    ) -> List[LLMHeaderDecision]:
        """
        Отправляет кандидатов в LLM и возвращает решение по каждому.
        """
        logger.debug(
            "HeaderClassifier.classify: кандидатов: %d", len(candidates)
        )

        if not candidates:
            logger.debug("Кандидатов нет, возвращаем пустой список")
            return []

        payload = [
            {
                "line_idx": c.line_idx,
                "text": c.text,
                "before": c.before,
                "after": c.after,
            }
            for c in candidates
        ]

        logger.debug("Первые 3 кандидата:")
        for i, c in enumerate(candidates[:3]):
            logger.debug(
                "  [%d] line_idx=%d, text='%s...'",
                i+1, c.line_idx, c.text[:50]
            )

        system_prompt = self.llm.load_prompt("classify_headers")
        logger.debug("Промпт загружен, длина: %d символов", len(system_prompt))

        task = (
            "Вот список кандидатов на заголовки с контекстом (JSON):\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        logger.debug("Задача сформирована, длина: %d символов", len(task))

        logger.debug("ДАННЫЕ ДЛЯ КЛАССИФИКАЦИИ: %d кандидатов", len(payload))
        if payload:
            example = json.dumps(payload[0], ensure_ascii=False)[:200]
            logger.debug("Пример первого кандидата: %s...", example)

        logger.debug("Отправка запроса к LLM...")
        raw = await self.llm.request(system_prompt, task)
        logger.debug("Ответ получен, длина: %d символов", len(raw))
        logger.debug("Ответ LLM (классификация): %s...", raw[:500])

        raw_str = raw.strip()
        if raw_str.startswith("```"):
            logger.debug("Обнаружен markdown code block, удаляем обёртку")
            raw_str = raw_str.strip("`")
            if raw_str.startswith("json"):
                raw_str = raw_str[len("json"):].lstrip()

        logger.debug("Парсинг JSON ответа...")
        data = json.loads(raw_str)
        logger.debug("JSON распарсен, результатов: %d", len(data['results']))

        decisions: List[LLMHeaderDecision] = []
        headers_found = 0
        for item in data["results"]:
            decisions.append(
                LLMHeaderDecision(
                    line_idx=item["line_idx"],
                    is_header=item["is_header"],
                    level=item.get("level"),
                    title=item.get("title"),
                )
            )
            if item["is_header"]:
                headers_found += 1

        logger.debug(
            "Найдено заголовков: %d/%d", headers_found, len(decisions)
        )
        return decisions
