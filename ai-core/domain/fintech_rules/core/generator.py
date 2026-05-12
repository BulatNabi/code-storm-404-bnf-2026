from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.common.client import LLMClient
from .chunker import process_document, estimate_tokens
from .prompts import PromptTemplates
from .utils import assign_ids, build_metadata, validate_rules
from shared.common.tags_loader import get_tags_loader

logger = logging.getLogger(__name__)


class RuleGenerator:
    """Генератор правил из нормативных документов."""

    def __init__(
        self,
        llm_client: LLMClient,
        max_tokens: int = 50_000,
        chunk_size: int = 40_000,
        overlap: int = 2_000,
        temperature: float = 0.0,
        response_max_tokens: int = 12_000,
        retries: int = 3,
    ):
        self.llm = llm_client
        self.max_tokens = max_tokens
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.temperature = temperature
        self.response_max_tokens = response_max_tokens
        self.retries = retries

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        text = tag.strip().lower()
        text = text.replace("-", "_").replace(" ", "_")
        text = re.sub(r"[^a-z0-9_]", "", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return text

    async def generate_rules(
        self,
        doc_path: str,
        rule_template: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        started = time.time()
        path = Path(doc_path)
        if not path.exists():
            return {
                "status": "error",
                "error": f"File not found: {doc_path}",
                "strategy": None,
                "rules": [],
                "metadata": build_metadata(doc_path, 0, 0, 0, 0, 0, 0),
            }

        try:
            strategy, chunks = process_document(
                file_path=doc_path,
                max_tokens=self.max_tokens,
                chunk_size=self.chunk_size,
                overlap=self.overlap,
            )
        except (ValueError, FileNotFoundError) as exc:
            return {
                "status": "error",
                "error": str(exc),
                "strategy": None,
                "rules": [],
                "metadata": build_metadata(doc_path, 0, 0, 0, 0, 0, 0),
            }

        llm_calls = 0
        rules: List[Dict[str, Any]] = []

        if strategy == "direct":
            logger.info("Using DIRECT strategy (single LLM call)")
            logger.debug(
                "Chunks count=%d, max_tokens=%d, chunk_size=%d, overlap=%d",
                len(chunks),
                self.max_tokens,
                self.chunk_size,
                self.overlap,
            )
            prompt = PromptTemplates.get_direct_prompt(document_text=chunks[0])
            logger.info(
                "Sending request to LLM (this may take 1-3 minutes)..."
            )
            raw = await self._ask_llm(prompt)
            llm_calls += 1
            logger.info("Received response from LLM, parsing rules...")
            try:
                rules = validate_rules(raw, allow_incomplete=False)
            except ValueError as exc:
                if "Invalid JSON from LLM" not in str(exc):
                    raise
                logger.warning("Invalid JSON from LLM, trying to repair...")
                repaired = await self._repair_json_array(raw)
                llm_calls += 1
                rules = validate_rules(repaired, allow_incomplete=False)
        else:
            logger.info(
                "Using MAP-REDUCE strategy: chunks=%d, max_tokens=%d",
                len(chunks),
                self.max_tokens,
            )
            candidates: List[Dict[str, Any]] = []
            for idx, chunk in enumerate(chunks):
                logger.debug(
                    "MAP chunk %d/%d: size=%d chars",
                    idx + 1,
                    len(chunks),
                    len(chunk),
                )
                prompt = PromptTemplates.get_map_prompt(
                    chunk_text=chunk,
                    chunk_id=idx,
                    total_chunks=len(chunks),
                )
                raw = await self._ask_llm(prompt)
                llm_calls += 1
                try:
                    candidates.extend(validate_rules(raw, allow_incomplete=True))
                except ValueError as exc:
                    if "Invalid JSON from LLM" not in str(exc):
                        raise
                    logger.warning("Invalid JSON from LLM in MAP, trying to repair...")
                    repaired = await self._repair_json_array(raw)
                    llm_calls += 1
                    candidates.extend(validate_rules(repaired, allow_incomplete=True))

            logger.info(
                "Map phase finished: candidates=%d, llm_calls=%d",
                len(candidates),
                llm_calls,
            )
            reduce_prompt = PromptTemplates.get_reduce_prompt(candidates)
            logger.info("Starting REDUCE phase...")
            reduced_raw = await self._ask_llm(reduce_prompt)
            llm_calls += 1
            try:
                rules = validate_rules(reduced_raw, allow_incomplete=False)
            except ValueError as exc:
                if "Invalid JSON from LLM" not in str(exc):
                    raise
                logger.warning("Invalid JSON from LLM in REDUCE, trying to repair...")
                repaired = await self._repair_json_array(reduced_raw)
                llm_calls += 1
                rules = validate_rules(repaired, allow_incomplete=False)
            logger.info("Reduce phase completed: final rules=%d", len(rules))

        # Присваиваем ID
        rules = assign_ids(rules)

        # Валидируем теги
        rules = self._validate_tags(rules)

        elapsed = time.time() - started
        chars = sum(len(c) for c in chunks)
        tokens = estimate_tokens("".join(chunks))
        metadata = build_metadata(
            doc_path=doc_path,
            doc_size_chars=chars,
            doc_size_tokens=tokens,
            chunks_count=len(chunks),
            rules_count=len(rules),
            processing_time=elapsed,
            llm_calls=llm_calls,
        )

        return {
            "status": "success",
            "strategy": strategy,
            "rules": rules,
            "metadata": metadata,
            "error": None,
        }

    def _validate_tags(
        self,
        rules: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Валидирует теги правил против tags.json.
        Пытается нормализовать/починить тег; если не получается — отбрасывает.
        """
        tags_loader = get_tags_loader()
        all_tags = tags_loader.get_all_tags()
        all_tags_set = set(all_tags)
        valid_rules = []

        for rule in rules:
            tag = rule.get("tag")
            if not tag:
                logger.warning(
                    "Rule %s has no tag, skipping", rule.get("rule_id")
                )
                continue

            if not isinstance(tag, str):
                continue

            normalized = self._normalize_tag(tag)
            if normalized in all_tags_set:
                if normalized != tag:
                    rule["tag"] = normalized
                valid_rules.append(rule)
                continue

            close = difflib.get_close_matches(normalized, all_tags, n=1, cutoff=0.86)
            if close:
                fixed = close[0]
                logger.info(
                    "Fixed tag for rule %s: '%s' -> '%s'",
                    rule.get("rule_id"),
                    tag,
                    fixed,
                )
                rule["tag"] = fixed
                valid_rules.append(rule)
                continue

            if not tags_loader.is_valid_tag(tag):
                logger.warning(
                    "Rule %s has invalid tag '%s', skipping. "
                    "Valid tags: %s...",
                    rule.get("rule_id"),
                    tag,
                    ", ".join(tags_loader.get_all_tags()[:5]),
                )
                continue

            valid_rules.append(rule)

        if len(valid_rules) < len(rules):
            logger.info(
                "Filtered out %s rules with invalid tags",
                len(rules) - len(valid_rules),
            )

        return valid_rules

    async def _ask_llm(self, prompt: str) -> str:
        last_error = None
        for attempt in range(self.retries):
            try:
                return await self.llm.request(
                    system_prompt=(
                        "Ты эксперт по нормативным документам. "
                        "Верни только JSON."
                    ),
                    task=prompt,
                    temperature=self.temperature,
                    max_tokens=self.response_max_tokens,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "LLM call failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.retries,
                    exc,
                )
                await asyncio.sleep(2**attempt)
        raise RuntimeError(f"LLM failed after retries: {last_error}")

    async def _repair_json_array(self, raw: str) -> str:
        task = (
            "Твоя задача: исправить ответ модели так, чтобы он стал валидным JSON МАССИВОМ.\n"
            "Верни только JSON массив, без пояснений и без markdown.\n"
            "Если невозможно восстановить, верни [].\n\n"
            "Вход:\n"
            "```\n"
            f"{raw}\n"
            "```\n"
        )
        return await self.llm.request(
            system_prompt="Ты исправляешь невалидный JSON. Верни только валидный JSON массив.",
            task=task,
            temperature=0.0,
            max_tokens=self.response_max_tokens,
            response_format=None,
        )
