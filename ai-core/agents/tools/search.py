"""Tools exposed to the LangGraph agent. Every callable here is registered
with `agent_executor.tools = [search_regulations, search_rules, ...]`."""

from __future__ import annotations

import json
from typing import Optional, List

from langchain_core.tools import tool

from ._es import EMBEDDING_DIMS, embed, get_es
from ._queries import build_hybrid_query, build_nested_rule_query
from .schemas import DocHit, RuleHit, RuleSearchHit, TagCount, QuoteVerification

import os
ES_INDEX = os.environ.get("ES_INDEX", "regtech-docs")


# ── 1. Hybrid search ───────────────────────────────────────────────────────

@tool
async def search_regulations(
    query: str,
    tags:       Optional[List[str]] = None,
    severities: Optional[List[str]] = None,
    sources:    Optional[List[str]] = None,
    top_k:      int = 5,
) -> str:
    """Поиск релевантных регуляторных документов в индексе.

    Hybrid retrieval: BM25 по полному тексту + dense vector (kNN) по
    эмбеддингу summary. Используй когда хочешь найти **документы** (целые
    законы, регламенты, нормативные акты), а не отдельные правила.

    Args:
        query: Свободное описание темы. Примеры:
            "обмен персональными данными между сервисами",
            "требования к KYC при онбординге",
            "уведомление ЦБ при инциденте безопасности".
        tags: Фильтр по регуляторным областям (any-of). Допустимые
            значения см. в `list_available_tags()`. Типичные:
            personal_data, aml_cft, kyc, payments, ai_scoring,
            cybersecurity, consumer_protection.
        severities: Фильтр по уровню критичности (any-of):
            "critical", "high", "medium", "low".
        sources: Фильтр по юрисдикции:
            "cbu" (ЦБ Узбекистана), "lex" (Lex.uz / РУз),
            "eurlex" (ЕС регламенты).
        top_k: Сколько документов вернуть. Дефолт 5, максимум 20.

    Returns:
        JSON-строка с массивом DocHit.
    """
    top_k = max(1, min(top_k, 20))
    query_vec = await embed(query)

    body = build_hybrid_query(
        query_text=query, query_vector=query_vec,
        tags=tags, severities=severities, sources=sources, top_k=top_k,
    )
    
    try:
        resp = await get_es().search(index=ES_INDEX, body=body)
    except Exception as e:
        return json.dumps({"error": f"Elasticsearch error: {str(e)}"})

    hits: list[DocHit] = []
    for h in resp.get("hits", {}).get("hits", []):
        src = h["_source"]
        hl  = (h.get("highlight") or {}).get("full_text") or []
        hits.append(DocHit(
            doc_id      = src["doc_id"],
            source      = src["source"],
            category    = src.get("category"),
            language    = src.get("language"),
            title       = src.get("title") or "",
            source_url  = src.get("source_url") or "",
            has_rules   = bool(src.get("has_rules")),
            rules_count = int(src.get("rules_count") or 0),
            tags        = src.get("tags") or [],
            severities  = src.get("severities") or [],
            score       = float(h.get("_score") or 0.0),
            highlights  = hl,
        ))

    return json.dumps([h.model_dump() for h in hits], ensure_ascii=False)


# ── 2. Search atomic rules ─────────────────────────────────────────────────

@tool
async def search_rules(
    query: str,
    tags:       Optional[List[str]] = None,
    severities: Optional[List[str]] = None,
    top_k:      int = 10,
) -> str:
    """Поиск конкретных правил (а не документов) по тексту требования.

    Используй когда нужны атомарные комплаенс-требования.

    Args:
        query: Описание требования.
        tags, severities: фильтры, как в `search_regulations`.
        top_k: Сколько правил вернуть. Дефолт 10, максимум 30.

    Returns:
        JSON-строка с массивом RuleSearchHit.
    """
    top_k = max(1, min(top_k, 30))
    body = build_nested_rule_query(
        query_text=query, query_vector=None,
        tags=tags, severities=severities, top_k=top_k,
    )
    
    try:
        resp = await get_es().search(index=ES_INDEX, body=body)
    except Exception as e:
        return json.dumps({"error": f"Elasticsearch error: {str(e)}"})

    out: list[RuleSearchHit] = []
    for h in resp.get("hits", {}).get("hits", []):
        src = h["_source"]
        inner = (h.get("inner_hits") or {}).get("rules", {}).get("hits", {}).get("hits", [])
        for ih in inner:
            r = ih["_source"]
            out.append(RuleSearchHit(
                doc_id     = src["doc_id"],
                source     = src["source"],
                doc_title  = src.get("title") or "",
                source_url = src.get("source_url") or "",
                rule = RuleHit(
                    rule_id             = r.get("rule_id", "unknown_rule_id"),
                    tag                 = r.get("tag"),
                    title               = r.get("title"),
                    requirement         = r.get("requirement", ""),
                    verification_method = r.get("verification_method"),
                    severity            = r.get("severity", "medium"),
                    positive_examples   = r.get("positive_examples", []),
                    negative_examples   = r.get("negative_examples", []),
                ),
                score = float(h.get("_score") or 0.0),
            ))

    return json.dumps([h.model_dump() for h in out], ensure_ascii=False)


# ── 3. Fetch one document ──────────────────────────────────────────────────

@tool
async def get_document(doc_id: str, include_full_text: bool = False) -> str:
    """Загрузить один документ целиком — со всеми его правилами и опционально полным текстом."""
    fields = [
        "doc_id", "source", "category", "language", "title",
        "source_url", "has_rules", "rules_count", "tags", "severities",
        "rules",
    ]
    if include_full_text:
        fields.append("full_text")

    try:
        resp = await get_es().get(index=ES_INDEX, id=doc_id, _source=fields)
    except Exception as exc:
        if "not_found" in str(exc).lower() or "404" in str(exc):
            return json.dumps({"error": "not_found", "doc_id": doc_id})
        return json.dumps({"error": f"Elasticsearch error: {str(exc)}"})

    src = resp["_source"]
    rules = [
        RuleHit(
            rule_id             = r.get("rule_id", "unknown_rule_id"),
            tag                 = r.get("tag"),
            title               = r.get("title"),
            requirement         = r.get("requirement", ""),
            verification_method = r.get("verification_method"),
            severity            = r.get("severity", "medium"),
            positive_examples   = r.get("positive_examples", []),
            negative_examples   = r.get("negative_examples", []),
        )
        for r in (src.get("rules") or [])
    ]
    doc = DocHit(
        doc_id      = src["doc_id"],
        source      = src["source"],
        category    = src.get("category"),
        language    = src.get("language"),
        title       = src.get("title") or "",
        source_url  = src.get("source_url") or "",
        has_rules   = bool(src.get("has_rules")),
        rules_count = int(src.get("rules_count") or 0),
        tags        = src.get("tags") or [],
        severities  = src.get("severities") or [],
        rules       = rules,
    )
    payload = doc.model_dump()
    if include_full_text:
        payload["full_text"] = src.get("full_text") or ""
    return json.dumps(payload, ensure_ascii=False)


# ── 4. Taxonomy ────────────────────────────────────────────────────────────

@tool
async def list_available_tags() -> str:
    """Перечислить все теги, которые реально присутствуют в индексе."""
    try:
        resp = await get_es().search(
            index=ES_INDEX,
            size=0,
            aggs={"tags": {"terms": {"field": "tags", "size": 50}}},
        )
    except Exception as e:
         return json.dumps({"error": f"Elasticsearch error: {str(e)}"})

    buckets = (resp.get("aggregations") or {}).get("tags", {}).get("buckets", [])
    out = [TagCount(tag=b["key"], count=int(b["doc_count"])) for b in buckets]
    return json.dumps([t.model_dump() for t in out], ensure_ascii=False)


# ── 5. Anti-hallucination quote check ──────────────────────────────────────

@tool
async def verify_quote(doc_id: str, quote: str) -> str:
    """Проверить, что цитата действительно встречается в указанном документе."""
    q = (quote or "").strip()
    if len(q) < 20:
        return json.dumps({"found": False, "in_field": None, "snippet": None,
                           "reason": "quote_too_short"})

    try:
        resp = await get_es().get(
            index=ES_INDEX, id=doc_id,
            _source=["full_text", "rules"],
        )
    except Exception:
        return json.dumps({"found": False, "in_field": None, "snippet": None,
                           "reason": "doc_not_found"})

    src = resp["_source"]
    full_text = src.get("full_text") or ""
    if q in full_text:
        i = full_text.find(q)
        snippet = full_text[max(0, i - 80): i + len(q) + 80]
        return json.dumps(QuoteVerification(
            found=True, in_field="full_text", snippet=snippet,
        ).model_dump(), ensure_ascii=False)

    for r in src.get("rules") or []:
        if q in (r.get("requirement") or ""):
            return json.dumps(QuoteVerification(
                found=True, in_field="rule.requirement", snippet=r.get("requirement"),
            ).model_dump(), ensure_ascii=False)
        if q in (r.get("title") or ""):
            return json.dumps(QuoteVerification(
                found=True, in_field="rule.title", snippet=r.get("title"),
            ).model_dump(), ensure_ascii=False)

    return json.dumps(QuoteVerification(found=False).model_dump(), ensure_ascii=False)
