from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    if "```" not in s:
        return s
    start = s.find("```")
    end = s.find("```", start + 3)
    if start != -1 and end != -1:
        inner = s[start + 3 : end]
        inner = inner.lstrip()
        if inner.startswith("json"):
            inner = inner[4:].lstrip()
        return inner.strip()
    return s


def _extract_json_array(text: str) -> list:
    s = _strip_code_fences(text)
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = s[start : end + 1].strip()
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON from LLM: {exc}") from exc
        if isinstance(data, list):
            return data

    raise ValueError("LLM output must be a list of rules")


def assign_ids(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Присваивает уникальные rule_id формата R-<TAG>-NNN

    Группирует правила по тегам и нумерует внутри каждого тега.
    Пример: R-SEC-001, R-SEC-002, R-AIT-001
    """
    # Группируем по тегам
    by_tag = defaultdict(list)
    for rule in rules:
        tag = rule.get("tag", "UNKNOWN")
        by_tag[tag].append(rule)

    result = []
    for tag, tag_rules in by_tag.items():
        # Создаем префикс из первых букв тега (max 3 символа)
        # security -> SEC, ai_technology -> AIT
        tag_parts = tag.split("_")
        if len(tag_parts) > 1:
            # ai_technology -> AIT (первая буква каждого слова)
            tag_prefix = "".join([p[0].upper() for p in tag_parts[:3]])
        else:
            # security -> SEC (первые 3 буквы)
            tag_prefix = tag[:3].upper()

        # Присваиваем ID
        for idx, rule in enumerate(tag_rules, 1):
            rule["rule_id"] = f"R-{tag_prefix}-{idx:03d}"
            # Удаляем служебные поля если есть
            rule.pop("incomplete", None)
            rule.pop("chunk_id", None)
            result.append(rule)

    return result


def validate_rules(raw: str, allow_incomplete: bool) -> List[Dict[str, Any]]:
    """
    Валидирует и очищает правила от LLM (новый формат).

    Args:
        raw: JSON-строка от LLM
        allow_incomplete: разрешить поле incomplete (для map-reduce)

    Returns:
        Список валидных правил
    """
    data = _extract_json_array(raw)

    cleaned: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        # Новая схема правила
        rule = {
            "rule_id": item.get("rule_id", "AUTO"),
            "tag": item.get("tag", ""),
            "title": item.get("title", ""),
            "requirement": item.get("requirement", ""),
            "verification_method": item.get("verification_method", ""),
            "positive_examples": item.get("positive_examples", []),
            "negative_examples": item.get("negative_examples", []),
            "severity": item.get("severity", "medium"),
            "source": item.get("source", {}),
        }

        # Служебные поля для map-reduce
        if allow_incomplete and "incomplete" in item:
            rule["incomplete"] = item.get("incomplete", False)
            rule["chunk_id"] = item.get("chunk_id", 0)

        # Валидация обязательных полей
        if not rule["requirement"]:
            logger.warning(f"Skipping rule without requirement: {rule.get('title', 'unknown')}")
            continue

        if not rule["tag"]:
            logger.warning(f"Skipping rule without tag: {rule.get('title', 'unknown')}")
            continue

        # Валидация примеров
        if not isinstance(rule["positive_examples"], list):
            rule["positive_examples"] = []

        if not isinstance(rule["negative_examples"], list):
            rule["negative_examples"] = []

        # Валидация severity
        if rule["severity"] not in ["critical", "high", "medium", "low"]:
            logger.warning(f"Invalid severity '{rule['severity']}' for rule {rule['title']}, defaulting to 'medium'")
            rule["severity"] = "medium"

        cleaned.append(rule)

    logger.info(f"Validated {len(cleaned)} rules from {len(data)} raw items")
    return cleaned


def build_metadata(
    doc_path: str,
    doc_size_chars: int,
    doc_size_tokens: int,
    chunks_count: int,
    rules_count: int,
    processing_time: float,
    llm_calls: int,
) -> Dict[str, Any]:
    return {
        "doc_path": doc_path,
        "doc_size_chars": doc_size_chars,
        "doc_size_tokens": doc_size_tokens,
        "chunks_count": chunks_count,
        "rules_count": rules_count,
        "processing_time": round(processing_time, 2),
        "llm_calls": llm_calls,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
