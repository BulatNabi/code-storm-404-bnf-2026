from pathlib import Path
from typing import Iterable, List

from .models import ChunkRef, Tag

PROMPT_PATH = Path(__file__).parent / "prompts" / "classification.txt"


def build_system_prompt(tags: Iterable[Tag]) -> str:
    """Загружает шаблон промпта и подставляет список тегов."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    tag_lines = "\n".join(f"- {t.tag_name}: {t.description or ''}" for t in tags)
    return template.replace("{{TAGS}}", tag_lines)


def build_task_payload(chunks: List[ChunkRef]) -> str:
    """Формирует user-запрос: список чанков для классификации."""
    items = []
    for c in chunks:
        items.append(
            {
                "id": c.id,
                "title": c.obj.get("title"),
                "level": c.level,
                "own_content": c.own_content,
                "path": c.obj.get("path"),
                "toc_number": c.obj.get("toc_number"),
            }
        )
    return {"chunks": items}

