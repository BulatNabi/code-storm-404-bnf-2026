from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rules_common.client.llm_client import LLMClient
from .chunker import process_document, estimate_tokens
from .prompts import PromptTemplates
from .utils import assign_ids, build_metadata, validate_rules
from rules_common.tags_loader import get_tags_loader

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
        response_max_tokens: int = 8_000,
        retries: int = 3,
    ):
        self.llm = llm_client
        self.max_tokens = max_tokens
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.temperature = temperature
        self.response_max_tokens = response_max_tokens
        self.retries = retries

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
            rules = validate_rules(raw, allow_incomplete=False)
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
                candidates.extend(validate_rules(raw, allow_incomplete=True))

            logger.info(
                "Map phase finished: candidates=%d, llm_calls=%d",
                len(candidates),
                llm_calls,
            )
            reduce_prompt = PromptTemplates.get_reduce_prompt(candidates)
            logger.info("Starting REDUCE phase...")
            reduced_raw = await self._ask_llm(reduce_prompt)
            llm_calls += 1
            rules = validate_rules(reduced_raw, allow_incomplete=False)
            logger.info("Reduce phase completed: final rules=%d", len(rules))

        # Присваиваем ID
        rules = assign_ids(rules)

        # Валидируем теги
        rules = self._validate_tags(rules)
        
        # Persistence is the caller's responsibility (rules_extractor worker
        # writes to Postgres). We just return the rules here.

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
        Валидирует теги правил. Если есть новые теги с описанием, добавляет их.
        Отбрасывает правила с невалидными тегами, если они не описаны.
        """
        tags_loader = get_tags_loader()
        valid_rules = []

        for rule in rules:
            tag = rule.get("tag")
            if not tag:
                logger.warning(
                    "Rule %s has no tag, skipping", rule.get("rule_id")
                )
                continue

            new_tag_def = rule.pop("new_tag_definition", None)

            if not tags_loader.is_valid_tag(tag):
                if new_tag_def and isinstance(new_tag_def, dict):
                    # Динамически добавляем новый тег
                    category = new_tag_def.get("category", "compliance")
                    description = new_tag_def.get("description", f"Автоматически добавленный тег: {tag}")
                    tags_loader.add_tag(tag, category, description)
                    logger.info("Dynamically added new tag: %s", tag)
                else:
                    logger.warning(
                        "Rule %s has invalid tag '%s' without new_tag_definition, skipping. ",
                        rule.get("rule_id"),
                        tag,
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
                    response_format=None,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "LLM call failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.retries,
                    exc,
                )
                time.sleep(2**attempt)
        raise RuntimeError(f"LLM failed after retries: {last_error}")
