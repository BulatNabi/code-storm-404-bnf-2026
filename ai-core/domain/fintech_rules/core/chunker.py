from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for ru/uz text."""
    return len(text) // 3


def _chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Naive chunking by symbols (~3 chars per token)."""
    char_chunk = chunk_size * 3
    char_overlap = overlap * 3

    chunks: List[str] = []
    start = 0

    while start < len(text):
        end = start + char_chunk
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - char_overlap
        if start <= 0:
            start = end

    return chunks


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1251")


def process_document(
    file_path: str,
    max_tokens: int = 50_000,
    chunk_size: int = 40_000,
    overlap: int = 2_000,
) -> Tuple[str, List[str]]:
    """
    Decide strategy and return chunks.

    Returns:
        strategy: "direct" | "map_reduce"
        chunks: list of text chunks (one element for direct)
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if chunk_size >= max_tokens:
        raise ValueError("chunk_size must be < max_tokens")
    if overlap >= chunk_size:
        raise ValueError("overlap must be < chunk_size")

    text = _read_text(path)
    tokens = estimate_tokens(text)
    logger.info("Document tokens estimate: %s", tokens)

    if tokens <= max_tokens:
        return "direct", [text]

    chunks = _chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    logger.info("Chunked document into %d parts", len(chunks))
    return "map_reduce", chunks

