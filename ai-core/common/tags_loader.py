"""Загрузчик тегов из schema/tags.json, общий для всех модулей."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set

TAGS_FILE = Path(__file__).parent.parent / "schema" / "tags.json"


class TagsLoader:
    """Единый загрузчик/валидатор тегов."""

    def __init__(self, tags_file: Path = TAGS_FILE):
        self.tags_file = tags_file
        self._tags_db: Dict[str, Dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.tags_file.exists():
            raise FileNotFoundError(f"Tags file not found: {self.tags_file}")
        tags_list = json.loads(self.tags_file.read_text(encoding="utf-8"))
        self._tags_db = {tag["tag_name"]: tag for tag in tags_list}

    def get_all_tags(self) -> List[str]:
        return list(self._tags_db.keys())

    def get_tag_info(self, tag_name: str) -> Dict | None:
        return self._tags_db.get(tag_name)

    def get_category(self, tag_name: str) -> str | None:
        tag_info = self._tags_db.get(tag_name)
        return tag_info.get("category") if tag_info else None

    def get_description(self, tag_name: str) -> str | None:
        tag_info = self._tags_db.get(tag_name)
        return tag_info.get("description") if tag_info else None

    def is_valid_tag(self, tag_name: str) -> bool:
        return tag_name in self._tags_db

    def validate_tags(self, tags: List[str]) -> tuple[bool, List[str]]:
        invalid = [tag for tag in tags if not self.is_valid_tag(tag)]
        return len(invalid) == 0, invalid

    def get_tags_by_category(self, category: str) -> List[str]:
        return [
            tag_name
            for tag_name, tag_info in self._tags_db.items()
            if tag_info.get("category") == category
        ]

    def get_all_categories(self) -> Set[str]:
        return {tag_info.get("category") for tag_info in self._tags_db.values()}

    def add_tag(self, tag_name: str, category: str, description: str) -> None:
        """Динамически добавляет новый тег и сохраняет в файл."""
        if tag_name not in self._tags_db:
            self._tags_db[tag_name] = {
                "tag_name": tag_name,
                "category": category,
                "description": description
            }
            self._save()

    def _save(self) -> None:
        """Сохраняет текущие теги в файл."""
        tags_list = list(self._tags_db.values())
        self.tags_file.write_text(
            json.dumps(tags_list, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def format_tags_for_prompt(self) -> str:
        tags_for_prompt = [
            {"tag": tag_name, "description": tag_info["description"]}
            for tag_name, tag_info in self._tags_db.items()
        ]
        return json.dumps(tags_for_prompt, ensure_ascii=False, indent=2)

    def group_rules_by_category(self, rules: List[Dict]) -> Dict[str, List[Dict]]:
        grouped: Dict[str, List[Dict]] = {}
        for rule in rules:
            tag_name = rule.get("tag")
            if not tag_name:
                continue
            category = self.get_category(tag_name) or "unknown"
            grouped.setdefault(category, []).append(rule)
        return grouped


_tags_loader = None


def get_tags_loader() -> TagsLoader:
    global _tags_loader
    if _tags_loader is None:
        _tags_loader = TagsLoader()
    return _tags_loader

