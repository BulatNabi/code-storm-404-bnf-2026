"""
Поиск заголовков: эвристики для обнаружения явных и потенциальных заголовков.
"""

import logging
import re
from typing import Optional

from .detection import is_toc_item

logger = logging.getLogger(__name__)


HEADER_RE = re.compile(r"^("
    r"#{1,6}\s+.+|"
    r"- \d+(?:\.\d+)*\s+.+|"
    r"\*\*\d+(?:\.\d+)*.*\*\*|"
    r"\*\*[А-ЯA-Z][А-ЯA-Z\s,]+\*\*|"
    r"\[.*\d+(?:\.\d+)+.*\]\(\.\)"
r")")


def _is_valid_numbered_header(line: str) -> bool:
    """Проверяет, является ли номерованная строка заголовком."""
    stripped = line.strip()

    if len(stripped) > 150:
        return False

    if stripped.endswith('.') and not re.match(r'^\d+(?:\.\d+)*\.\s*$', stripped):
        return False

    match = re.match(r'^(\d+)\.\s+(.+)$', stripped)
    if match:
        num, text = match.groups()
        if len(text) > 30:
            return False
        if '.' not in num and len(text.split()) > 5:
            return False

    if any(word in stripped.lower() for word in [
        'мб,', 'формат файла', 'максимальный размер', 'обязательное поля',
        'случае удаления', 'должен быть проведен', 'необходимо для'
    ]):
        return False

    return True


class HeaderFinder:
    """Поиск заголовков в документе."""

    def find_explicit(
        self,
        lines: list[str],
        skip_lines: Optional[set[int]] = None
    ) -> set[int]:
        """Строки, которые уже выглядят как заголовки (# или 1.2.3 ...)."""
        if skip_lines is None:
            skip_lines = set()

        logger.debug(f"find_explicit: строк в документе: {len(lines)}")
        if skip_lines:
            logger.debug(f"Пропускаем строк: {len(skip_lines)}")

        header_lines = set()
        for i, line in enumerate(lines):
            if i in skip_lines:
                continue

            stripped = line.strip()

            if is_toc_item(stripped):
                continue

            text_only = stripped.lstrip('#').strip()

            if re.match(r'^\d{4}(-\d{4})?\s*(г\.?|год)?\.?$', text_only, re.IGNORECASE):
                continue

            if re.match(r'^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$', text_only):
                continue

            if HEADER_RE.match(stripped):
                header_lines.add(i)
            elif (re.match(r'^\d+(?:\.\d+)*\.?\s+[А-ЯA-Z]', stripped)
                  and _is_valid_numbered_header(stripped)):
                header_lines.add(i)

        logger.debug(f"Найдено явных заголовков: {len(header_lines)}")
        return header_lines

    def find_candidates(
        self,
        lines: list[str],
        explicit_header_lines: set[int],
        skip_lines: Optional[set[int]] = None
    ) -> list[tuple[int, str]]:
        """Подозрительные строки, которые МОГУТ быть заголовками."""
        if skip_lines is None:
            skip_lines = set()

        logger.debug("find_candidates: начало работы")
        logger.debug(f"Явных заголовков (пропускаем): {len(explicit_header_lines)}")
        if skip_lines:
            logger.debug(f"Строк титульной/оглавления (пропускаем): {len(skip_lines)}")

        candidates: list[tuple[int, str]] = []
        stats = {
            "total_lines": len(lines),
            "skipped_explicit": 0,
            "skipped_toc": 0,
            "skipped_length": 0,
            "skipped_no_russian": 0,
            "skipped_blacklist_exact": 0,
            "skipped_blacklist_pattern": 0,
            "skipped_list_marker": 0,
            "skipped_table_context": 0,
            "skipped_no_empty_line": 0,
        }

        blacklist_exact = {
            "String", "YYYY-MM-DD", "Date", "Number", "File", "Text",
            "Автоматический из ИСР", "Заполняемое поле", "Загрузка файлов",
            "Выпадающие список", "Заполняемое поле", "Копия решений",
            "Краткий текст решения", "Дата решения",
            "Номер документа (лицензии/разрешения)"
        }

        blacklist_patterns = [
            r"^\d+$",
            r"^[a-zA-Z]+$",
            r"^\[.*\]\(\.\)$",
            r"^Таблица\s+№?\d+$",
            r"^Рисунок\s+№?\d+",
            r"^\d+\.\s*$",
            r"^\d{4}(-\d{4})?\s*(г\.?|год)?\.?$",
            r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$",
        ]

        for i, line in enumerate(lines):
            stripped = line.strip()

            if i in explicit_header_lines:
                stats["skipped_explicit"] += 1
                continue

            if i in skip_lines:
                stats["skipped_toc"] += 1
                continue

            if not (10 <= len(stripped) <= 60):
                stats["skipped_length"] += 1
                continue

            if not re.search(r'[а-яёА-ЯЁ]', stripped):
                stats["skipped_no_russian"] += 1
                continue

            if stripped in blacklist_exact:
                stats["skipped_blacklist_exact"] += 1
                continue

            if any(re.match(pattern, stripped) for pattern in blacklist_patterns):
                stats["skipped_blacklist_pattern"] += 1
                continue

            if stripped.startswith(("-", "*", ">", "|", "\u2022")):
                stats["skipped_list_marker"] += 1
                continue

            context_lines = []
            for j in range(max(0, i-2), min(len(lines), i+3)):
                if j != i and lines[j].strip():
                    context_lines.append(len(lines[j].strip()))

            if (len(context_lines) >= 3
                and sum(1 for x in context_lines if x < 20)
                    >= len(context_lines) * 0.7):
                stats["skipped_table_context"] += 1
                continue

            prev_empty = (i == 0) or (not lines[i - 1].strip())
            next_empty = (i == len(lines) - 1) or (not lines[i + 1].strip())
            if not (prev_empty or next_empty):
                stats["skipped_no_empty_line"] += 1
                continue

            candidates.append((i, stripped))

        logger.debug(f"Найдено кандидатов: {len(candidates)}")
        logger.debug("Статистика фильтрации:")
        for reason, count in stats.items():
            if count > 0:
                logger.debug(f"  - {reason}: {count}")

        return candidates

    def infer_header_level(self, line: str) -> int:
        """
        Определяет уровень заголовка:
        - markdown: количество # в начале
        - '3.1.2 Текст': уровень = количество частей номера (3 = уровень 3)
        - **ТЕКСТ**: уровень = 1 (структурный заголовок без номера)
        """
        stripped = line.lstrip()

        if stripped.startswith("#"):
            return len(stripped) - len(stripped.lstrip("#"))

        m = re.match(r"^(\d+(?:\.\d+)*)\s+.+", stripped)
        if m:
            number = m.group(1)
            return number.count(".") + 1

        if re.match(r"^\*\*[А-ЯA-Z][А-ЯA-Z\s,]+\*\*$", stripped):
            return 1

        return 1

    def get_section_depth(self, text: str) -> Optional[int]:
        """
        Извлекает реальную глубину вложенности из номера раздела.
        '3.1.5.2.1 Название' -> 5
        '## 2.3 Текст' -> 2
        'Название без номера' -> None
        """
        stripped = text.strip().lstrip("#*-").strip()
        m = re.match(r"^(\d+(?:\.\d+)*)", stripped)
        if m:
            number = m.group(1)
            return number.count(".") + 1
        return None
