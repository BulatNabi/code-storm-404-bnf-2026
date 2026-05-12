"""
Построение дерева чанков на основе иерархии заголовков.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import ChunkNode

logger = logging.getLogger(__name__)


class ChunkTreeBuilder:
    """
    Строитель дерева чанков на основе иерархии от LLM.

    Использование:
        builder = ChunkTreeBuilder()
        tree = builder.build_from_corrected_headers(
            corrected_headers=llm_output,
            lines=document_lines
        )
        json_output = builder.to_json(tree)
    """

    def __init__(self, path_separator: str = " -> "):
        self.path_separator = path_separator
        self._chunk_counter = 0

    def _generate_id(self) -> str:
        """Генерирует уникальный ID для чанка (chunk_001, chunk_002, ...)."""
        self._chunk_counter += 1
        return f"chunk_{self._chunk_counter:03d}"

    def _reset_counter(self):
        """Сбрасывает счётчик ID перед построением нового дерева."""
        self._chunk_counter = 0

    def _assign_ids_in_order(self, top_level_chunks: List[ChunkNode]):
        """
        Присваивает ID всем чанкам в порядке обхода в глубину.
        Вызывается после построения дерева чтобы frontmatter был chunk_001.
        """
        self._reset_counter()

        def assign_recursive(node: ChunkNode):
            node.id = self._generate_id()
            for child in node.children:
                assign_recursive(child)

        for chunk in top_level_chunks:
            assign_recursive(chunk)

    def build_from_corrected_headers(
        self,
        corrected_headers: List[Dict],
        lines: List[str],
        appendix_start_line: Optional[int] = None
    ) -> List[ChunkNode]:
        """
        Построение дерева из уже исправленных заголовков (structured output от LLM).

        Args:
            corrected_headers: Список словарей [{line_idx, level, title}, ...]
            lines: Строки исходного документа
            appendix_start_line: Номер строки начала приложений (или None)

        Returns:
            Список чанков верхнего уровня (без ROOT)
        """
        sorted_headers = sorted(
            corrected_headers,
            key=lambda h: h.get("line_idx", 0)
        )

        root, nodes = self._build_tree(sorted_headers)

        self._calculate_boundaries(nodes, len(lines), appendix_start_line)

        first_header_line = nodes[0].line_idx if nodes else 0
        self._handle_frontmatter(root, lines, first_header_line)

        self._handle_appendix(root, lines, appendix_start_line)

        self._extract_content_recursive(root, lines)

        self._build_paths(root)

        self._assign_ids_in_order(root.children)

        return root.children

    def _build_tree(
        self,
        headers_with_idx: List[Dict]
    ) -> tuple[ChunkNode, List[ChunkNode]]:
        """
        Строит дерево чанков из списка заголовков.

        Returns:
            (root, nodes) - корневой узел (внутренний) и плоский список узлов
        """
        root = ChunkNode(
            id="_root",
            title="ROOT",
            level=0,
            line_idx=-1,
            start_line=0,
            end_line=0,
            own_content="",
        )

        stack = [root]
        nodes: List[ChunkNode] = []

        for h in headers_with_idx:
            toc_number = ""
            m = re.match(r'^(\d+(?:\.\d+)*)', h['title'])
            if m:
                toc_number = m.group(1)

            node = ChunkNode(
                id="",
                title=h['title'],
                level=h['level'],
                line_idx=h.get('line_idx', -1),
                start_line=h.get('line_idx', -1),
                end_line=-1,
                own_content="",
                toc_number=toc_number
            )

            while len(stack) > 1 and stack[-1].level >= node.level:
                stack.pop()

            parent = stack[-1]
            node.parent = parent
            parent.children.append(node)
            nodes.append(node)
            stack.append(node)

        return root, nodes

    def _calculate_boundaries(
        self,
        nodes: List[ChunkNode],
        total_lines: int,
        appendix_start_line: Optional[int] = None
    ):
        """Определяет start_line и end_line для каждого узла."""
        for i, node in enumerate(nodes):
            node.start_line = node.line_idx

            end_line = total_lines - 1

            if appendix_start_line is not None:
                end_line = min(end_line, appendix_start_line - 1)

            for j in range(i + 1, len(nodes)):
                if nodes[j].level <= node.level:
                    end_line = min(end_line, nodes[j].line_idx - 1)
                    break

            node.end_line = end_line

    def _handle_frontmatter(
        self,
        root: ChunkNode,
        lines: List[str],
        first_header_line: int
    ):
        """Создаёт чанк для титульника/метаданных документа."""
        if first_header_line > 0:
            frontmatter_content = '\n'.join(lines[:first_header_line])

            if frontmatter_content.strip():
                frontmatter_node = ChunkNode(
                    id="",
                    title="Титульник",
                    level=1,
                    line_idx=0,
                    start_line=0,
                    end_line=first_header_line - 1,
                    own_content=frontmatter_content,
                    is_frontmatter=True
                )
                frontmatter_node.parent = root
                root.children.insert(0, frontmatter_node)

    def _handle_appendix(
        self,
        root: ChunkNode,
        lines: List[str],
        appendix_start_line: Optional[int]
    ):
        """Создаёт чанк для приложений в конце документа."""
        if appendix_start_line is not None and appendix_start_line < len(lines):
            appendix_content = '\n'.join(lines[appendix_start_line:])

            if appendix_content.strip():
                appendix_node = ChunkNode(
                    id="",
                    title="Приложения",
                    level=1,
                    line_idx=appendix_start_line,
                    start_line=appendix_start_line,
                    end_line=len(lines) - 1,
                    own_content=appendix_content,
                    is_appendix=True
                )
                appendix_node.parent = root
                root.children.append(appendix_node)

    def _extract_content_recursive(self, node: ChunkNode, lines: List[str]):
        """Рекурсивно извлекает контент для узла и всех его потомков."""
        if node.level == 0:
            for child in node.children:
                self._extract_content_recursive(child, lines)
            return

        if node.is_frontmatter or node.is_appendix:
            return

        own_end = node.end_line
        if node.children:
            first_child_start = node.children[0].start_line
            if first_child_start > node.start_line:
                own_end = first_child_start - 1

        if node.start_line >= 0 and own_end >= node.start_line:
            node.own_content = '\n'.join(lines[node.start_line:own_end + 1])
        else:
            node.own_content = ""

        for child in node.children:
            self._extract_content_recursive(child, lines)

    def _build_paths(self, node: ChunkNode, parent_path: str = ""):
        """Рекурсивно строит иерархические пути для всех узлов."""
        if node.level == 0:
            node.path = ""
        else:
            clean_title = node.clean_title()
            if parent_path:
                node.path = f"{parent_path}{self.path_separator}{clean_title}"
            else:
                node.path = clean_title

        for child in node.children:
            self._build_paths(child, node.path)

    def _iter_nodes(self, nodes: List[ChunkNode]):
        for n in nodes:
            yield n
            yield from self._iter_nodes(n.children)

    def to_json(
        self,
        chunks: List[ChunkNode],
        document_name: str = "",
        include_content: bool = True
    ) -> Dict[str, Any]:
        """Конвертирует список чанков в JSON-совместимый словарь."""
        total_chunks = 0
        max_depth = 0
        for n in self._iter_nodes(chunks):
            total_chunks += 1
            if n.level > max_depth:
                max_depth = n.level

        return {
            "document_name": document_name,
            "total_chunks": total_chunks,
            "max_depth": max_depth,
            "chunks": [
                chunk.to_dict(include_content=include_content)
                for chunk in chunks
            ]
        }

    def save_json(
        self,
        chunks: List[ChunkNode],
        output_path: Path,
        document_name: str = "",
        include_content: bool = True
    ):
        """Сохраняет чанки в JSON файл."""
        data = self.to_json(chunks, document_name, include_content)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Чанки сохранены: {output_path}")
        logger.info(f"  Всего чанков: {data['total_chunks']}")
        logger.info(f"  Максимальная глубина: {data['max_depth']}")

    def print_tree(self, chunks: List[ChunkNode], max_depth: int = None):
        """Выводит дерево в консоль в читаемом формате."""
        def print_node(node: ChunkNode, indent: int = 0):
            if max_depth is not None and node.level > max_depth:
                return

            prefix = "  " * indent
            size_info = f"({node.get_content_size()} chars)"
            frontmatter_mark = " [FRONTMATTER]" if node.is_frontmatter else ""
            appendix_mark = " [APPENDIX]" if node.is_appendix else ""

            logger.info(
                f"{prefix}[{node.id}] {'#' * node.level} "
                f"{node.title} {size_info}{frontmatter_mark}{appendix_mark}"
            )

            for child in node.children:
                print_node(child, indent + 1)

        for chunk in chunks:
            print_node(chunk)
