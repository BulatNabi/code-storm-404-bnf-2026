import json
import logging
from typing import Dict, Iterable, List, Sequence, Set

from shared.common.client.llm_client import LLMClient

from .models import Evidence, Rule, RuleResult, ValidationChunkResult

logger = logging.getLogger(__name__)


def _collect_tags(chunk: Dict) -> Set[str]:
    tags = set()
    for key in ("tags", "tags_direct", "tags_aggregated"):
        value = chunk.get(key) or []
        if isinstance(value, list):
            tags.update([t for t in value if isinstance(t, str)])
    return tags


def _flatten(chunks: Sequence[Dict]) -> Iterable[Dict]:
    for node in chunks:
        yield node
        for child in _flatten(node.get("children", []) or []):
            yield child


class Validator:
    def __init__(
        self,
        llm_client: LLMClient,
        validation_prompt: str,
        tags_allowed: Set[str],
        max_tokens: int = 8000,
    ) -> None:
        self.llm_client = llm_client
        self.validation_prompt = validation_prompt
        self.tags_allowed = tags_allowed
        self.max_tokens = max_tokens

    async def validate(
        self,
        rules: List[Rule],
        classified_payload: Dict,
    ) -> List[RuleResult]:
        chunks = classified_payload.get("chunks", [])
        results: List[RuleResult] = []

        logger.info("Начинаем валидацию: правил=%d", len(rules))

        for rule in rules:
            if not rule.is_active:
                continue
            if rule.tag not in self.tags_allowed:
                logger.warning(
                    "Правило %s пропущено: неизвестный тег %s",
                    rule.rule_id,
                    rule.tag,
                )
                continue

            target_chunks = self._chunks_for_tag(chunks, rule.tag)
            logger.debug(
                "Правило %s (%s): найдено чанков %d",
                rule.rule_id,
                rule.tag,
                len(target_chunks),
            )
            if not target_chunks:
                results.append(
                    RuleResult(
                        rule_id=rule.rule_id,
                        tag=rule.tag,
                        severity=rule.severity,
                        rule_title=rule.title,
                        status="no_chunks",
                        overall_confidence=0.0,
                        chunk_results=[],
                        no_chunks=True,
                    )
                )
                continue

            chunk_results = []
            for chunk in target_chunks:
                logger.debug(
                    "LLM проверка: правило %s, чанк %s (%s)",
                    rule.rule_id,
                    chunk.get("id"),
                    chunk.get("path") or chunk.get("title"),
                )
                chunk_results.append(await self._validate_chunk(rule, chunk))

            rule_result = self._aggregate_rule(rule, chunk_results)
            logger.info(
                "Результат правила %s: статус=%s, чанков=%d, доверие=%.2f",
                rule.rule_id,
                rule_result.status,
                len(chunk_results),
                rule_result.overall_confidence,
            )
            results.append(rule_result)

        logger.info("Валидация завершена: правил обработано=%d", len(results))
        return results

    def _chunks_for_tag(self, chunks: Sequence[Dict], tag: str) -> List[Dict]:
        result = []
        for chunk in _flatten(chunks):
            # Проверяем только tags_direct, игнорируем агрегированные
            tags_direct = chunk.get("tags_direct") or []
            if isinstance(tags_direct, list) and tag in tags_direct:
                result.append(chunk)
        return result

    async def _validate_chunk(
        self,
        rule: Rule,
        chunk: Dict,
    ) -> ValidationChunkResult:
        chunk_id = chunk.get("id", "")
        chunk_title = chunk.get("title") or chunk.get("clean_title")
        chunk_path = chunk.get("path") or ""
        chunk_content = chunk.get("own_content") or ""

        task = self._build_task(
            rule,
            chunk_id=chunk_id,
            chunk_title=chunk_title,
            chunk_content=chunk_content,
            chunk_path=chunk_path,
        )

        try:
            raw = await self.llm_client.request(
                system_prompt=(
                    "Ты эксперт по валидации технических заданий. "
                    "Верни только JSON."
                ),
                task=task,
                temperature=0.0,
                max_tokens=self.max_tokens,
                response_format=None,
            )
            parsed = self._parse_llm_json(raw)
        except Exception:
            logger.exception(
                "LLM ошибка для правила %s и чанка %s",
                rule.rule_id,
                chunk_id,
            )
            parsed = None

        if not parsed:
            return ValidationChunkResult(
                chunk_id=chunk_id,
                chunk_title=chunk_title,
                chunk_path=chunk_path,
                status="fail",
                found_evidence=[],
                missing_requirements=[
                    "LLM вернул невалидный JSON - невозможно проверить"
                ],
                confidence=0.0,
                explanation="Ошибка при обработке LLM",
            )

        # Parse new format
        status = parsed.get("status", "fail")
        if status not in ("pass", "partial", "fail"):
            status = "fail"

        # Parse evidence
        evidence_list = []
        for ev_raw in parsed.get("found_evidence", []):
            if isinstance(ev_raw, dict):
                evidence_list.append(
                    Evidence(
                        text=ev_raw.get("text", ""),
                        section=ev_raw.get("section", ""),
                        matches=ev_raw.get("matches", ""),
                    )
                )

        missing_requirements = parsed.get("missing_requirements", [])
        if not isinstance(missing_requirements, list):
            missing_requirements = []

        confidence = float(parsed.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))  # Clamp to [0.0, 1.0]

        explanation = parsed.get("explanation", "")

        return ValidationChunkResult(
            chunk_id=chunk_id,
            chunk_title=chunk_title,
            chunk_path=chunk_path,
            status=status,
            found_evidence=evidence_list,
            missing_requirements=missing_requirements,
            confidence=confidence,
            explanation=explanation,
        )

    def _aggregate_rule(
        self,
        rule: Rule,
        chunk_results: List[ValidationChunkResult],
    ) -> RuleResult:
        """
        Агрегирует результаты проверки чанков в общий результат по правилу.

        Логика определения статуса:
        - fail: хотя бы один чанк fail
        - partial: хотя бы один чанк partial (и нет fail)
        - pass: все чанки pass

        overall_confidence: среднее значение confidence из всех чанков
        """
        if not chunk_results:
            return RuleResult(
                rule_id=rule.rule_id,
                tag=rule.tag,
                severity=rule.severity,
                rule_title=rule.title,
                status="fail",
                overall_confidence=0.0,
                chunk_results=[],
                no_chunks=False,
            )

        # Подсчитываем количество каждого статуса
        partial_count = sum(
            1 for c in chunk_results if c.status == "partial"
        )
        fail_count = sum(1 for c in chunk_results if c.status == "fail")

        # Определяем общий статус
        if fail_count > 0:
            status = "fail"
        elif partial_count > 0:
            status = "partial"
        else:
            status = "pass"

        # Вычисляем среднюю уверенность
        total_confidence = sum(c.confidence for c in chunk_results)
        overall_confidence = total_confidence / len(chunk_results)

        return RuleResult(
            rule_id=rule.rule_id,
            tag=rule.tag,
            severity=rule.severity,
            rule_title=rule.title,
            status=status,
            overall_confidence=overall_confidence,
            chunk_results=chunk_results,
            no_chunks=False,
        )

    def _build_task(
        self,
        rule: Rule,
        chunk_id: str,
        chunk_title: str,
        chunk_content: str,
        chunk_path: str = "",
    ) -> str:
        """
        Форматирует validation_prompt с данными правила и чанка.

        Ожидаемые плейсхолдеры в self.validation_prompt:
        {rule_id}, {tag}, {title}, {severity}, {requirement},
        {verification_method}, {positive_examples}, {negative_examples},
        {chunk_title}, {chunk_path}, {chunk_content}
        """
        # Форматируем примеры в читаемый вид
        positive_examples_formatted = "\n".join(
            f"- {ex}" for ex in rule.positive_examples
        )
        negative_examples_formatted = "\n".join(
            f"- {ex}" for ex in rule.negative_examples
        )

        return self.validation_prompt.format(
            rule_id=rule.rule_id,
            tag=rule.tag,
            title=rule.title,
            severity=rule.severity,
            requirement=rule.requirement,
            verification_method=rule.verification_method,
            positive_examples=positive_examples_formatted,
            negative_examples=negative_examples_formatted,
            chunk_title=chunk_title or "(без названия)",
            chunk_path=chunk_path or "(без пути)",
            chunk_content=chunk_content or "(пустой контент)",
        )

    def _parse_llm_json(self, raw: str) -> Dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Невалидный JSON от LLM: %s", raw[:500])
            return {}


async def validate_rules_for_file(
    llm_client: LLMClient,
    validation_prompt: str,
    rules: List[Rule],
    classified_payload: Dict,
    tags_allowed: Set[str],
) -> List[RuleResult]:
    validator = Validator(
        llm_client=llm_client,
        validation_prompt=validation_prompt,
        tags_allowed=tags_allowed,
    )
    return await validator.validate(
        rules=rules,
        classified_payload=classified_payload,
    )
