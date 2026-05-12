"""
Пакет chunker - парсинг и чанкирование технических заданий.

Основные компоненты:
- LLMClient: клиент для работы с LLM (Yandex Qwen)
- DocumentDetector: детекция границ документа (TOC, титульная, приложения)
- HeaderFinder: поиск заголовков (эвристики)
- HeaderClassifier: LLM классификация кандидатов в заголовки
- HierarchyCorrector: восстановление иерархии заголовков
- ChunkTreeBuilder: построение дерева чанков
- DocumentParser: главный пайплайн парсинга

Использование:
    from core.chunker import DocumentParser, LLMClient
    
    client = LLMClient.from_env()
    parser = DocumentParser(client)
    chunks = await parser.parse(Path("document.md"), max_depth=3)
"""

from .models import (
    ChunkNode,
    LLMHeaderCandidate,
    LLMHeaderDecision,
    DEFAULT_MAX_DEPTH,
)
from .llm_client import LLMClient
from .detection import DocumentDetector
from .headers import HeaderFinder
from .classification import HeaderClassifier
from .hierarchy import HierarchyCorrector
from .tree_builder import ChunkTreeBuilder
from .pipeline import DocumentParser

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "ChunkNode",
    "LLMHeaderCandidate",
    "LLMHeaderDecision",
    "LLMClient",
    "DocumentDetector",
    "HeaderFinder",
    "HeaderClassifier",
    "HierarchyCorrector",
    "ChunkTreeBuilder",
    "DocumentParser",
]

