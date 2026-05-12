"""
Модели данных для chunker.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Константы
DEFAULT_MAX_DEPTH = 3


@dataclass
class LLMHeaderCandidate:
    """Кандидат в заголовки для LLM (строка + контекст)."""
    line_idx: int
    text: str
    before: str
    after: str


@dataclass
class LLMHeaderDecision:
    """Решение LLM по строке-кандидату."""
    line_idx: int
    is_header: bool
    level: Optional[int] = None
    title: Optional[str] = None


@dataclass
class ChunkNode:
    """
    Узел дерева чанков.

    Attributes:
        id: Уникальный идентификатор (chunk_001, chunk_002, ...)
        title: Заголовок раздела (как в документе)
        level: Уровень вложенности (1 = #, 2 = ##, ...)
        line_idx: Индекс строки в исходном файле (0-based)
        start_line: Начальная строка контента
        end_line: Конечная строка контента
        own_content: Контент ТОЛЬКО этого узла (до первого дочернего)
        children: Список дочерних узлов
        parent: Ссылка на родительский узел (None для корня)
        path: Иерархический путь ("Общие сведения -> Полное наименование")
        toc_number: Номер раздела из оглавления ("1.2.3")
        is_frontmatter: True если это титульник/метаданные документа
        is_appendix: True если это приложение в конце документа
        tags: Список тегов для классификации
        is_classified: Флаг классификации
        classified_time: Время классификации
        metadata: Дополнительные метаданные
    """
    id: str
    title: str
    level: int
    line_idx: int
    start_line: int
    end_line: int
    own_content: str = ""
    children: List['ChunkNode'] = field(default_factory=list)
    parent: Optional['ChunkNode'] = None
    path: str = ""
    toc_number: str = ""
    is_frontmatter: bool = False
    is_appendix: bool = False
    tags: List[str] = field(default_factory=list)
    is_classified: bool = False
    classified_time: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def clean_title(self) -> str:
        """Возвращает заголовок без номера раздела."""
        return re.sub(r'^\d+(?:\.\d+)*\.?\s*', '', self.title).strip()

    def get_content_size(self) -> int:
        """Возвращает размер own_content в символах."""
        return len(self.own_content.strip())

    def to_dict(
        self,
        include_content: bool = True,
        include_parent: bool = False
    ) -> Dict[str, Any]:
        """Конвертирует узел в словарь для JSON сериализации."""
        result = {
            "id": self.id,
            "title": self.title,
            "clean_title": self.clean_title(),
            "level": self.level,
            "line_idx": self.line_idx,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "path": self.path,
            "toc_number": self.toc_number,
            "is_frontmatter": self.is_frontmatter,
            "is_appendix": self.is_appendix,
            "tags": self.tags,
            "is_classified": self.is_classified,
            "classified_time": (
                self.classified_time.isoformat()
                if self.classified_time else None
            ),
            "children_count": len(self.children),
        }

        if include_content:
            result["own_content"] = self.own_content
            result["own_content_size"] = self.get_content_size()

        if include_parent and self.parent:
            result["parent_id"] = self.parent.id
            result["parent_title"] = self.parent.title

        result["children"] = [
            child.to_dict(
                include_content=include_content,
                include_parent=include_parent
            )
            for child in self.children
        ]

        return result
