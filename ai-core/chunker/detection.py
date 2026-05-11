"""
Детекция границ документа: титульная страница, TOC, приложения.
"""

import json
import logging
import re
from typing import Optional

from .llm_client import LLMClient

logger = logging.getLogger(__name__)


def is_toc_item(line: str) -> bool:
    """Проверяет, является ли строка пунктом оглавления."""
    if re.search(r'\[.*\]\(.*\)', line):
        return True
    return False


class DocumentDetector:
    """Детектор границ документа (TOC, титульная, приложения)."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def find_content_start(self, lines: list[str]) -> Optional[int]:
        """
        Эвристически находит line_idx первого контентного заголовка.

        Returns:
            line_idx первого контентного заголовка или None
        """
        structural_keywords = [
            "ТЕРМИНЫ", "ОПРЕДЕЛЕНИЯ", "ВВЕДЕНИЕ", "ПРИЛОЖЕНИЕ",
            "GLOSSARY", "TERMS", "DEFINITIONS", "INTRODUCTION"
        ]

        first_numbered = None
        first_structural = None

        for i, line in enumerate(lines):
            stripped = line.strip()

            if is_toc_item(stripped):
                continue

            stripped = stripped.lstrip("#*-").strip()

            if first_numbered is None and re.match(
                r'^1[\.\s]+[А-ЯA-Z]', stripped
            ):
                first_numbered = i

            if first_structural is None:
                stripped_upper = stripped.upper()
                if any(kw in stripped_upper for kw in structural_keywords):
                    context_start = max(0, i - 5)
                    context_end = min(len(lines), i + 5)
                    context_lines = lines[context_start:context_end]
                    toc_links_count = sum(
                        1 for ln in context_lines if is_toc_item(ln.strip())
                    )
                    if toc_links_count < 3:
                        first_structural = i

        candidates = []
        if first_numbered is not None:
            candidates.append(first_numbered)
        if first_structural is not None:
            candidates.append(first_structural)

        if candidates:
            return min(candidates)
        return None

    def find_last_level1_header(
        self,
        lines: list[str],
        skip_lines: set[int]
    ) -> Optional[int]:
        """Находит последний заголовок первого уровня (N Название)."""
        last_level1 = None

        for i, line in enumerate(lines):
            if i in skip_lines:
                continue

            stripped = line.strip().lstrip("#*-").strip()

            if re.match(r'^\d+\s+[А-ЯA-Z]', stripped):
                number_part = stripped.split()[0]
                if '.' not in number_part:
                    last_level1 = i

        return last_level1

    async def detect_title_and_toc(
        self,
        lines: list[str],
        start_idx: int,
        end_idx: int
    ) -> dict:
        """
        Отправляет подозрительную зону в LLM для определения титульной и TOC.

        Returns:
            {
                "title_page_range": [0, 3],  # [start, end) или null
                "toc_range": [4, 34],        # [start, end) или null
            }
        """
        zone_lines = []
        for i in range(start_idx, min(end_idx, len(lines))):
            zone_lines.append(f"{i}: {lines[i]}")
        zone_text = "\n".join(zone_lines)

        system_prompt = self.llm.load_prompt("detect_title_toc")
        task = f"Вот фрагмент документа:\n\n{zone_text}"

        logger.debug(
            "Отправка запроса к LLM для определения титульной/оглавления..."
        )
        result = await self.llm.request(system_prompt, task)
        logger.debug("Ответ получен, длина: %d символов", len(result))

        raw_str = result.strip()
        if raw_str.startswith("```"):
            raw_str = raw_str.strip("`")
            if raw_str.startswith("json"):
                raw_str = raw_str[4:].strip()

        try:
            data = json.loads(raw_str)
            return data
        except json.JSONDecodeError as e:
            logger.error("Не удалось распарсить JSON: %s", e)
            logger.error("Ответ LLM: %s...", raw_str[:500])
            return {"title_page_range": None, "toc_range": None}

    async def detect_appendices(
        self,
        lines: list[str],
        last_level1_line: int
    ) -> dict:
        """
        Определяет начало приложений от последнего раздела level=1 до конца.

        Returns:
            {"appendix_start_line": 450} или {"appendix_start_line": null}
        """
        zone_lines = []
        for i in range(last_level1_line, len(lines)):
            zone_lines.append(f"{i}: {lines[i]}")
        zone_text = "\n".join(zone_lines)

        system_prompt = self.llm.load_prompt("detect_appendices")
        task = f"Вот фрагмент документа:\n\n{zone_text}"

        logger.debug("Отправка запроса к LLM для определения приложений...")
        result = await self.llm.request(system_prompt, task)
        logger.debug(
            "Ответ LLM на определение приложений: %s...", result[:150]
        )

        raw_str = result.strip()
        if raw_str.startswith("```"):
            raw_str = raw_str.strip("`")
            if raw_str.startswith("json"):
                raw_str = raw_str[4:].strip()

        try:
            data = json.loads(raw_str)
            return data
        except json.JSONDecodeError as e:
            logger.error(
                "Не удалось распарсить JSON ответ о приложениях: %s", e
            )
            logger.error("Ответ LLM: %s...", raw_str[:500])
            return {"appendix_start_line": None}
