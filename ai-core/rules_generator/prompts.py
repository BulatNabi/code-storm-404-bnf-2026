from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any

from common.tags_loader import get_tags_loader

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


class PromptTemplates:
    """Prompt helpers for rule generation."""

    @staticmethod
    def get_direct_prompt(document_text: str) -> str:
        template = _load_prompt("direct")
        tags_loader = get_tags_loader()
        tags_json = tags_loader.format_tags_for_prompt()
        return template.format(
            document_text=document_text,
            tags_json=tags_json
        )

    @staticmethod
    def get_map_prompt(
        chunk_text: str,
        chunk_id: int,
        total_chunks: int,
    ) -> str:
        template = _load_prompt("map")
        tags_loader = get_tags_loader()
        tags_json = tags_loader.format_tags_for_prompt()
        return template.format(
            chunk_text=chunk_text,
            chunk_id=chunk_id,
            chunk_id_plus=chunk_id + 1,
            total_chunks=total_chunks,
            tags_json=tags_json,
        )

    @staticmethod
    def get_reduce_prompt(candidates: List[Dict[str, Any]]) -> str:
        template = _load_prompt("reduce")
        candidates_json = json.dumps(candidates, ensure_ascii=False, indent=2)
        return template.format(candidates_json=candidates_json)
