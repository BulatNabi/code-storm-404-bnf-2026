import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from common.client.llm_client import LLMClient

from .batching import estimate_tokens, group_by_level1, split_by_token_limit
from .models import ChunkRef, Tag
from .prompt import build_system_prompt, build_task_payload

logger = logging.getLogger(__name__)


class ChunkClassifier:
    def __init__(
        self,
        llm_client: LLMClient,
        tags: Iterable[Tag],
        max_tokens: int = 80000,
        min_parent_content: int = 120,
    ):
        self.llm = llm_client
        self.tags = list(tags)
        self.allowed_tags = {t.tag_name for t in tags}
        self.max_tokens = max_tokens
        self.min_parent_content = min_parent_content
        self.system_prompt = build_system_prompt(self.tags)

    async def classify(self, doc: dict) -> dict:
        roots = doc.get("chunks") if isinstance(doc, dict) else doc
        if not roots:
            return doc

        flat = list(self._flatten(roots))
        targets = [c for c in flat if self._needs_direct(c)]
        logger.info(
            "Классификация: всего чанков=%d, целевых=%d",
            len(flat),
            len(targets),
        )

        batches = self._make_batches(flat, targets)
        logger.info("Сформировано партий для LLM: %d", len(batches))
        for batch in batches:
            if not batch:
                continue
            task_payload = build_task_payload(batch)
            logger.debug("Отправляем партию: чанков=%d", len(batch))
            if task_payload["chunks"]:
                logger.debug(
                    "Пример чанка: %s",
                    json.dumps(
                        task_payload["chunks"][0],
                        ensure_ascii=False,
                    )[:500],
                )
            raw = await self.llm.request(
                system_prompt=self.system_prompt,
                task=json.dumps(task_payload, ensure_ascii=False),
                temperature=0.0,
            )
            logger.debug(
                "LLM raw response (first 500 chars): %s",
                (raw or "")[:500],
            )
            self._apply_llm_output(batch, raw)

        self._aggregate(flat)
        self._finalize(flat)
        return doc

    def _flatten(
        self,
        nodes: List[dict],
        parent_id: Optional[str] = None,
    ) -> Iterable[ChunkRef]:
        for node in nodes:
            ref = ChunkRef(obj=node, parent_id=parent_id)
            yield ref
            for child in ref.children:
                yield from self._flatten([child], parent_id=ref.id)

    def _needs_direct(self, chunk: ChunkRef) -> bool:
        is_leaf = chunk.children_count == 0
        has_content = len(chunk.own_content) >= self.min_parent_content
        return is_leaf or has_content

    def _make_batches(
        self,
        flat: List[ChunkRef],
        targets: List[ChunkRef],
    ) -> List[List[ChunkRef]]:
        total_tokens = estimate_tokens(targets)
        logger.info(
            "Оценка токенов для целевых чанков: ~%d",
            total_tokens,
        )
        if total_tokens <= self.max_tokens:
            return [targets]
        groups = group_by_level1(flat, targets)
        batches: List[List[ChunkRef]] = []
        for group in groups:
            group_tokens = estimate_tokens(group)
            logger.debug(
                "Группа level1: чанков=%d, токены~%d",
                len(group),
                group_tokens,
            )
            if group_tokens <= self.max_tokens:
                batches.append(group)
            else:
                split = split_by_token_limit(group, self.max_tokens)
                logger.debug(
                    "Группа разбита на подбатчи: %d шт.",
                    len(split),
                )
                batches.extend(split)
        return batches

    def _apply_llm_output(self, batch: List[ChunkRef], raw: str) -> None:
        # Очистка от markdown code fence (```json ... ```)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Убираем первую строку (```json или ```)
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Убираем последнюю строку (```)
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error("Не удалось распарсить ответ LLM: %s", e)
            logger.debug("Очищенный ответ: %s", cleaned[:500])
            return
        # Поддержка двух форматов: массив объектов или {"chunks": [...]}
        items = parsed
        if isinstance(parsed, dict) and "chunks" in parsed:
            items = parsed.get("chunks")
        if not isinstance(items, list):
            logger.error("Неожиданный формат ответа LLM: %s", type(items))
            return

        by_id = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            raw_tags = item.get("tags", [])
            norm_tags = []
            if isinstance(raw_tags, list):
                for t in raw_tags:
                    if isinstance(t, str):
                        norm_tags.append({"name": t})
                    elif isinstance(t, dict):
                        norm_tags.append(
                            {
                                "name": t.get("name"),
                                "reason": t.get("reason"),
                                "confidence": t.get("confidence"),
                            }
                        )
            by_id[cid] = norm_tags

        logger.debug("Получены теги для id: %s", list(by_id.keys()))
        logger.debug("Разрешенные теги: %s", sorted(self.allowed_tags))
        for ref in batch:
            tag_objs = by_id.get(ref.id, [])
            names = []
            reasons = {}
            confidences = {}
            unknown = []
            for t in tag_objs:
                name = t.get("name")
                if not name:
                    continue
                if name not in self.allowed_tags:
                    unknown.append(name)
                    continue
                names.append(name)
                if t.get("reason"):
                    reasons[name] = t["reason"]
                if t.get("confidence") is not None:
                    confidences[name] = t["confidence"]
            if unknown:
                logger.warning(
                    "Обнаружены неизвестные теги для %s: %s",
                    ref.id,
                    unknown,
                )
            clean = sorted(set(names))
            if clean:
                ref.obj["tags_direct"] = clean
                if reasons:
                    ref.obj["tags_reasons"] = reasons
                if confidences:
                    ref.obj["tags_confidence"] = confidences
            else:
                logger.debug("Для чанка %s теги пусты", ref.id)

    def _aggregate(self, flat: List[ChunkRef]) -> None:
        by_parent: defaultdict[str, List[ChunkRef]] = defaultdict(list)
        for ref in flat:
            if ref.parent_id:
                by_parent[ref.parent_id].append(ref)

        # bottom-up by level
        for ref in sorted(flat, key=lambda c: c.level, reverse=True):
            children = by_parent.get(ref.id, [])
            counter: Counter = Counter()
            for ch in children:
                counter.update(ch.obj.get("tags_direct", []))
                counter.update(ch.obj.get("tags_aggregated", []))
            if counter:
                ref.obj["tags_aggregated"] = sorted(counter.keys())
                ref.obj["tags_stats"] = dict(counter)

        for ref in flat:
            direct = set(ref.obj.get("tags_direct", []))
            aggregated = set(ref.obj.get("tags_aggregated", []))
            tags = sorted(direct | aggregated)
            ref.obj["tags"] = tags
            if not tags:
                logger.debug("Итоговые теги пусты для чанка %s", ref.id)

    def _finalize(self, flat: List[ChunkRef]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for ref in flat:
            ref.obj["is_classified"] = True
            ref.obj["classified_time"] = now
