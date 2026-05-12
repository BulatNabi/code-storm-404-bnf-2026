"""
Главный пайплайн парсинга документов.
"""

import logging
import re
from pathlib import Path
from typing import List, Optional

from .llm_client import LLMClient
from .models import ChunkNode, DEFAULT_MAX_DEPTH
from .detection import DocumentDetector
from .headers import HeaderFinder
from .classification import HeaderClassifier, build_llm_candidates
from .hierarchy import HierarchyCorrector
from .tree_builder import ChunkTreeBuilder

logger = logging.getLogger(__name__)


def read_lines(path: Path) -> list[str]:
    """Читает файл и возвращает список строк."""
    text = path.read_text(encoding="utf-8")
    return text.splitlines()


def normalize_for_dedup(title: str) -> str:
    """Нормализует заголовок для сравнения при дедупликации."""
    text = title.strip().lstrip("#*-").strip()
    text = text.strip("*").strip()
    text = re.sub(r"^(\d+)\.\s", r"\1 ", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _is_garbage_header(title: str) -> bool:
    """Проверяет, является ли заголовок мусорным (не структурным)."""
    text = title.strip().lstrip("#*-").strip().strip("*").strip()
    if re.match(r"^\d+", text):
        return False
    words = text.split()
    if len(words) <= 2 and text.isupper():
        valid_structural = [
            "ОГЛАВЛЕНИЕ", "СОДЕРЖАНИЕ", "ТЕРМИНЫ", "ВВЕДЕНИЕ",
            "ЗАКЛЮЧЕНИЕ", "ПРИЛОЖЕНИЕ", "СПИСОК ИСПОЛНИТЕЛЕЙ",
            "СПИСОК", "ИСПОЛНИТЕЛИ"
        ]
        if not any(v in text for v in valid_structural):
            return True
    return False


class DocumentParser:
    """Главный класс парсинга документов."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.detector = DocumentDetector(llm_client)
        self.header_finder = HeaderFinder()
        self.classifier = HeaderClassifier(llm_client)
        self.hierarchy = HierarchyCorrector(llm_client)
        self.tree_builder = ChunkTreeBuilder()

    async def parse(
        self,
        md_path: Path,
        max_depth: int = DEFAULT_MAX_DEPTH
    ) -> List[ChunkNode]:
        """
        Полный пайплайн парсинга документа.

        Args:
            md_path: Путь к markdown файлу
            max_depth: Максимальная глубина вложенности заголовков

        Returns:
            Список чанков верхнего уровня
        """
        if max_depth < 1:
            raise ValueError(
                f"max_depth должен быть >= 1, получено: {max_depth}"
            )
        if max_depth > 6:
            logger.warning(
                "max_depth=%d может быть избыточной. Рекомендуется 2-4.",
                max_depth
            )

        if not md_path.exists():
            raise FileNotFoundError(f"Не найден файл: {md_path}")

        lines = read_lines(md_path)

        skip_lines, appendix_start_line = await self._detect_document_zones(
            lines
        )

        final_headers = await self._find_and_classify_headers(
            lines, skip_lines, max_depth
        )

        corrected_headers = await self._correct_hierarchy(
            final_headers, max_depth
        )

        chunks = self.tree_builder.build_from_corrected_headers(
            corrected_headers=corrected_headers,
            lines=lines,
            appendix_start_line=appendix_start_line
        )

        return chunks

    async def _detect_document_zones(
        self,
        lines: list[str]
    ) -> tuple[set[int], Optional[int]]:
        """Определяет титульную страницу, TOC и приложения."""
        logger.info("=== ОПРЕДЕЛЕНИЕ ТИТУЛЬНОЙ И ОГЛАВЛЕНИЯ ===")

        skip_lines: set[int] = set()
        appendix_start_line = None

        content_start = self.detector.find_content_start(lines)

        if content_start is not None and content_start > 1:
            logger.info(
                "[ЭВРИСТИКА] Первый контентный заголовок на строке %d:",
                content_start
            )
            logger.info("  %s", lines[content_start][:80])

            logger.info("[LLM] Анализируем строки 0-%d...", content_start)
            toc_data = await self.detector.detect_title_and_toc(
                lines=lines,
                start_idx=0,
                end_idx=content_start
            )

            if toc_data.get("title_page_range"):
                start, end = toc_data["title_page_range"]
                skip_lines.update(range(start, end))
                logger.info("  Титульная страница: строки %d-%d", start, end-1)

            if toc_data.get("toc_range"):
                start, end = toc_data["toc_range"]
                skip_lines.update(range(start, end))
                logger.info("  Оглавление: строки %d-%d", start, end-1)

            if skip_lines:
                logger.info(
                    "[РЕЗУЛЬТАТ] Пропускаем %d строк", len(skip_lines)
                )
            else:
                logger.info("[РЕЗУЛЬТАТ] Титульная и оглавление не найдены")
        else:
            logger.info("[INFO] Контент начинается с первых строк")

        logger.info("=" * 60)

        logger.info("=== ОПРЕДЕЛЕНИЕ ПРИЛОЖЕНИЙ ===")

        last_level1 = self.detector.find_last_level1_header(lines, skip_lines)

        if last_level1 is not None:
            logger.info(
                "[ЭВРИСТИКА] Последний заголовок level=1 на строке %d:",
                last_level1
            )
            logger.info("  %s", lines[last_level1][:80])

            logger.info(
                "[LLM] Анализируем строки %d-%d...",
                last_level1, len(lines)-1
            )
            appendix_data = await self.detector.detect_appendices(
                lines, last_level1
            )

            if appendix_data.get("appendix_start_line") is not None:
                start = appendix_data["appendix_start_line"]
                appendix_start_line = start
                appendix_lines = set(range(start, len(lines)))
                skip_lines.update(appendix_lines)
                logger.info("  Приложения: строки %d-%d", start, len(lines)-1)
                logger.info(
                    "[РЕЗУЛЬТАТ] Найдены приложения, пропускаем %d строк",
                    len(appendix_lines)
                )
            else:
                logger.info("[РЕЗУЛЬТАТ] Приложения не найдены")
        else:
            logger.info("[INFO] Не найден последний заголовок level=1")

        logger.info("=" * 60)

        return skip_lines, appendix_start_line

    async def _find_and_classify_headers(
        self,
        lines: list[str],
        skip_lines: set[int],
        max_depth: int
    ) -> list[dict]:
        """Находит и классифицирует заголовки."""
        explicit = self.header_finder.find_explicit(lines, skip_lines)
        candidates = self.header_finder.find_candidates(
            lines, explicit, skip_lines
        )

        logger.info("=== ЯВНЫЕ ЗАГОЛОВКИ (эвристика) ===")
        for i in sorted(explicit):
            logger.info("[%4d] %s", i+1, lines[i])

        logger.info("=== ПОДОЗРИТЕЛЬНЫЕ СТРОКИ (эвристика) ===")
        for line_num, text in candidates:
            logger.info("[%4d] %s", line_num+1, text)

        llm_candidates = build_llm_candidates(lines, skip_lines)
        decisions = await self.classifier.classify(llm_candidates)

        logger.info("=== РЕШЕНИЕ LLM ПО КАНДИДАТАМ ===")
        for d in decisions:
            mark = "HEADER" if d.is_header else "NO"
            level_str = f"lvl={d.level}" if d.level is not None else ""
            title_str = f"-> {d.title}" if d.title else ""
            original = lines[d.line_idx].strip()
            logger.info(
                "[%4d] %-6s %-7s %s %s",
                d.line_idx+1, mark, level_str, original, title_str
            )

        final_headers: list[dict] = []

        for idx in sorted(explicit):
            line = lines[idx].strip()
            level = self.header_finder.infer_header_level(line)
            final_headers.append({
                "line_idx": idx,
                "source": "explicit",
                "level": level,
                "title": line,
            })

        for d in decisions:
            if not d.is_header:
                continue
            if d.line_idx in explicit:
                continue

            line = lines[d.line_idx].strip()
            title = d.title or line
            level = d.level or 3

            final_headers.append({
                "line_idx": d.line_idx,
                "source": "llm",
                "level": level,
                "title": title,
            })

        final_headers.sort(key=lambda h: h["line_idx"])

        final_headers = self._filter_headers(final_headers, max_depth)

        logger.info(
            "=== ИТОГОВЫЙ СПИСОК ЗАГОЛОВКОВ (глубина <= %d) ===", max_depth
        )
        for h in final_headers:
            idx = h["line_idx"]
            level = h["level"]
            src = h["source"]
            title = h["title"]
            depth = self.header_finder.get_section_depth(title)
            depth_str = f"d={depth}" if depth else "d=?"
            logger.info(
                "[%4d] lvl=%d %s (%s) %s",
                idx+1, level, depth_str, src, title
            )

        return final_headers

    def _filter_headers(
        self, headers: list[dict], max_depth: int
    ) -> list[dict]:
        """Фильтрует заголовки по глубине, дедупликация и удаление мусора."""
        logger.debug("Всего заголовков перед фильтрацией: %d", len(headers))

        filtered_headers = []
        skipped_count = 0

        for h in headers:
            depth = self.header_finder.get_section_depth(h["title"])
            if depth is None or depth <= max_depth:
                filtered_headers.append(h)
            else:
                skipped_count += 1
                if skipped_count <= 5:
                    logger.debug(
                        "[SKIP depth=%d] %s...", depth, h['title'][:70]
                    )

        if skipped_count > 0:
            logger.info(
                "Пропущено заголовков глубже уровня %d: %d",
                max_depth, skipped_count
            )

        logger.info("=== АНАЛИЗ ДУБЛИКАТОВ (до дедупликации) ===")

        title_occurrences: dict[str, list[dict]] = {}
        for h in filtered_headers:
            norm = normalize_for_dedup(h["title"])
            if norm not in title_occurrences:
                title_occurrences[norm] = []
            title_occurrences[norm].append(h)

        duplicates = {
            norm: hdrs
            for norm, hdrs in title_occurrences.items()
            if len(hdrs) > 1
        }

        if duplicates:
            logger.info("Найдено %d групп дубликатов:", len(duplicates))
            for norm, hdrs in sorted(
                duplicates.items(),
                key=lambda x: x[1][0]["line_idx"]
            ):
                logger.debug("  Группа: '%s'", norm)
                for i, h in enumerate(hdrs, 1):
                    logger.debug(
                        "    [%d] line_idx=%4d | %s",
                        i, h['line_idx'], h['title'][:80]
                    )

                if len(hdrs) >= 2:
                    first_dup_line_idx = hdrs[1]["line_idx"]
                    logger.debug(
                        "    -> Первый дубликат на line_idx=%d",
                        first_dup_line_idx
                    )
        else:
            logger.info("Дубликатов не найдено")

        seen_titles: dict[str, dict] = {}
        for h in filtered_headers:
            norm = normalize_for_dedup(h["title"])
            if norm in seen_titles:
                if h["line_idx"] > seen_titles[norm]["line_idx"]:
                    seen_titles[norm] = h
            else:
                seen_titles[norm] = h

        dedup_headers = list(seen_titles.values())
        dedup_headers.sort(key=lambda h: h["line_idx"])

        dedup_count = len(filtered_headers) - len(dedup_headers)
        if dedup_count > 0:
            logger.info("Удалено дубликатов из оглавления: %d", dedup_count)

        garbage_count = 0
        clean_headers = []
        for h in dedup_headers:
            if _is_garbage_header(h["title"]):
                garbage_count += 1
                if garbage_count <= 5:
                    logger.debug("[SKIP garbage] %s", h['title'][:50])
            else:
                clean_headers.append(h)

        if garbage_count > 0:
            logger.info("Удалено мусорных заголовков: %d", garbage_count)

        return clean_headers

    async def _correct_hierarchy(
        self,
        headers: list[dict],
        max_depth: int
    ) -> list[dict]:
        """Исправляет иерархию заголовков через LLM."""
        hierarchy_input = [
            {
                "line_idx": h["line_idx"],
                "level": h["level"],
                "title": h["title"],
                "source": h["source"],
            }
            for h in headers
        ]

        logger.info("=== ИСПРАВЛЕНИЕ ИЕРАРХИИ (LLM STRUCTURED OUTPUT) ===")

        logger.debug("ВСЕ ВХОДНЫЕ ДАННЫЕ ДЛЯ LLM (первые 15):")
        for i, h in enumerate(hierarchy_input[:15]):
            logger.debug(
                "  [%d] line_idx=%4d, level=%d, title=%s",
                i+1, h['line_idx'], h['level'], h['title'][:70]
            )
        if len(hierarchy_input) > 15:
            logger.debug("  ... всего %d заголовков", len(hierarchy_input))

        corrected_headers = await self.hierarchy.correct(
            hierarchy_input, max_depth
        )

        logger.info(
            "=== ИСПРАВЛЕННАЯ ИЕРАРХИЯ ОТ LLM (%d заголовков) ===",
            len(corrected_headers)
        )
        for h in corrected_headers[:10]:
            logger.info(
                "  [%4d] lvl=%d %s...",
                h['line_idx'], h['level'], h['title'][:60]
            )
        if len(corrected_headers) > 10:
            logger.info("  ... и ещё %d заголовков", len(corrected_headers)-10)

        return corrected_headers
