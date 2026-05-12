from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from .models import ChunkRef


def estimate_tokens(chunks: Iterable[ChunkRef]) -> int:
    """Грубая оценка числа токенов по own_content."""
    return sum(len(c.own_content) // 4 + 50 for c in chunks)


def _build_parent_map(chunks: Iterable[ChunkRef]) -> Dict[str, Optional[str]]:
    return {c.id: c.parent_id for c in chunks if c.id}


def _top_level1(chunk: ChunkRef, parents: Dict[str, Optional[str]], by_id: Dict[str, ChunkRef]) -> Optional[str]:
    current = chunk
    while current:
        if current.level == 1:
            return current.id
        parent_id = parents.get(current.id)
        if not parent_id:
            return None
        current = by_id.get(parent_id)
    return None


def group_by_level1(flat_chunks: List[ChunkRef], targets: List[ChunkRef]) -> List[List[ChunkRef]]:
    """Разбивает целевые чанки по поддеревьям level=1."""
    by_id = {c.id: c for c in flat_chunks if c.id}
    parents = _build_parent_map(flat_chunks)
    groups: Dict[Optional[str], List[ChunkRef]] = defaultdict(list)
    for chunk in targets:
        key = _top_level1(chunk, parents, by_id)
        groups[key].append(chunk)
    return list(groups.values())


def split_by_token_limit(chunks: List[ChunkRef], max_tokens: int) -> List[List[ChunkRef]]:
    """Гриди-нарезка списка чанков на подбатчи до лимита токенов."""
    batches: List[List[ChunkRef]] = []
    current: List[ChunkRef] = []
    current_tokens = 0
    for ch in chunks:
        size = len(ch.own_content) // 4 + 50
        if current and current_tokens + size > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(ch)
        current_tokens += size
    if current:
        batches.append(current)
    return batches

