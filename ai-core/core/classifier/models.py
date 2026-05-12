from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Tag:
    tag_name: str
    category: Optional[str] = None
    description: Optional[str] = None


@dataclass
class Rule:
    rule_id: str
    tag: str
    title: str
    what_to_check: str
    pass_if: List[str]
    severity: str
    is_active: bool
    look_for_examples: List[str] = field(default_factory=list)
    fail_if: List[str] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class ChunkRef:
    """Плоская ссылка на чанк."""

    obj: dict
    parent_id: Optional[str]

    @property
    def id(self) -> str:
        return self.obj.get("id")

    @property
    def level(self) -> int:
        return int(self.obj.get("level", 0))

    @property
    def own_content(self) -> str:
        return self.obj.get("own_content") or ""

    @property
    def children(self) -> list:
        return self.obj.get("children") or []

    @property
    def children_count(self) -> int:
        if "children_count" in self.obj:
            return int(self.obj.get("children_count") or 0)
        return len(self.children)

