# `ai-core` — LangChain Tools для работы с регуляторным индексом

Готовые к копированию `@tool`-функции, которые LangGraph-агент использует
для retrieval из Elasticsearch. Подробности устройства самой системы — в
[`architecture.md`](architecture.md).

> **TL;DR.** Агент имеет доступ к 5 инструментам:
> - `search_regulations(query, tags?, severities?, sources?, top_k?)` — hybrid BM25+kNN поиск документов
> - `search_rules(query, tags?, severities?, top_k?)` — поиск атомарных правил
> - `get_document(doc_id, include_full_text?)` — забрать один документ целиком
> - `list_available_tags()` — узнать какие теги вообще есть в индексе
> - `verify_quote(doc_id, quote)` — substring-проверка цитаты (анти-галлюцинация)

---

## 1. Зависимости

```bash
pip install \
  langchain-core>=0.3 \
  langgraph>=0.2 \
  elasticsearch>=8.13,<9 \
  openai>=1.50 \
  pydantic>=2.5 \
  anthropic>=0.39        # для самого LLM-агента; tools зависимостей не требуют
```

## 2. Конфигурация (`.env` сервиса `ai-core`)

```bash
# Elasticsearch (тот же индекс, что наполняет ETL)
ES_URL=http://localhost:9200
ES_INDEX=regtech-docs

# Embeddings — обязательно та же модель и base URL что в ETL,
# иначе расстояния в dense_vector станут несопоставимыми.
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-…                    # OpenRouter key
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMS=1536

# LLM-агент (Claude как primary)
ANTHROPIC_API_KEY=sk-ant-…
AGENT_MODEL=claude-3-5-sonnet-20241022
```

**Если `ai-core` крутится в docker compose в той же сети что
`regtech_elasticsearch`:** `ES_URL=http://regtech_elasticsearch:9200`. Из
хоста — `http://localhost:9200`.

---

## 3. Структура модуля

```
ai-core/
├── app/
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── _es.py             # ES singleton + embed singleton
│   │   ├── _queries.py        # build_hybrid_query, build_nested_rule_query
│   │   ├── search.py          # @tool функции — то что регистрируется в агенте
│   │   └── schemas.py         # Pydantic-модели tool результатов
│   ├── agent/
│   │   ├── graph.py           # LangGraph build_agent()
│   │   └── prompts.py
│   └── main.py                # FastAPI :8001
└── requirements.txt
```

---

## 4. Подключение к Elasticsearch (`app/tools/_es.py`)

Synchronous-free, async-first. Один singleton на процесс — LangGraph
переиспользует его на все запросы.

```python
"""Shared async ES client and embedding client. Built once per process,
reused by every @tool call."""

from __future__ import annotations

import os
from typing import Optional

from elasticsearch import AsyncElasticsearch
from openai import AsyncOpenAI

# ── Elasticsearch ──────────────────────────────────────────────────────────

_es: Optional[AsyncElasticsearch] = None


def get_es() -> AsyncElasticsearch:
    """Singleton AsyncElasticsearch client. Safe to call from any coroutine."""
    global _es
    if _es is None:
        _es = AsyncElasticsearch(
            os.environ.get("ES_URL", "http://localhost:9200"),
            request_timeout=30,
            max_retries=2,
            retry_on_timeout=True,
        )
    return _es


async def close_es() -> None:
    global _es
    if _es is not None:
        await _es.close()
        _es = None


# ── Embeddings (OpenRouter, OpenAI-compatible) ─────────────────────────────

_openai: Optional[AsyncOpenAI] = None


def get_embedding_client() -> AsyncOpenAI:
    global _openai
    if _openai is None:
        _openai = AsyncOpenAI(
            api_key=os.environ["LLM_API_KEY"],
            base_url=os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
            timeout=30.0,
        )
    return _openai


EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "openai/text-embedding-3-small")
EMBEDDING_DIMS  = int(os.environ.get("EMBEDDING_DIMS", "1536"))
MAX_INPUT_CHARS = 24_000           # ~8k tokens, same cap as ETL embedding_client


async def embed(text: str) -> list[float]:
    """Embed a single query into a 1536-dim vector matching the ETL index."""
    client = get_embedding_client()
    resp = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text[:MAX_INPUT_CHARS] if text else " ",
    )
    return list(resp.data[0].embedding)


# ── FastAPI lifecycle hook ─────────────────────────────────────────────────

# В main.py:
#
#     from contextlib import asynccontextmanager
#     from fastapi import FastAPI
#     from app.tools._es import close_es
#
#     @asynccontextmanager
#     async def lifespan(app: FastAPI):
#         yield
#         await close_es()
#
#     app = FastAPI(lifespan=lifespan)
```

---

## 5. Построение запросов (`app/tools/_queries.py`)

Хелперы, чтобы не дублировать длинные ES DSL в каждом `@tool`.

```python
"""Helpers that translate tool arguments into Elasticsearch DSL bodies."""

from __future__ import annotations

from typing import Any


def build_hybrid_query(
    query_text: str,
    query_vector: list[float],
    tags:       list[str] | None,
    severities: list[str] | None,
    sources:    list[str] | None,
    top_k:      int,
) -> dict[str, Any]:
    """Hybrid BM25 + dense kNN search.

    `bool.filter` is used for hard filters (sources, tag/severity arrays at
    the top level). `bool.should` mixes a BM25 `match` on `full_text` with
    a `knn` clause on the `embedding` field — ES sums their normalized
    scores. `boost: 1.5` on kNN weights it slightly above BM25 because the
    Track-4 evaluation criterion explicitly tests semantic recall.
    """
    filters: list[dict] = []
    if sources:
        filters.append({"terms": {"source": sources}})
    if tags:
        filters.append({"terms": {"tags": tags}})
    if severities:
        filters.append({"terms": {"severities": severities}})

    return {
        "size": top_k,
        "_source": [
            "doc_id", "source", "category", "language", "title",
            "source_url", "has_rules", "rules_count", "tags", "severities",
            # `rules` is heavy — return it on demand via get_document() instead.
        ],
        "query": {
            "bool": {
                "filter": filters,
                "should": [
                    {"match": {"full_text": {"query": query_text}}},
                    {
                        "knn": {
                            "field": "embedding",
                            "query_vector": query_vector,
                            "num_candidates": max(50, top_k * 5),
                            "boost": 1.5,
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        },
        "highlight": {
            "fields": {
                "full_text": {
                    "fragment_size": 220,
                    "number_of_fragments": 3,
                    "pre_tags":  ["<mark>"],
                    "post_tags": ["</mark>"],
                }
            }
        },
    }


def build_nested_rule_query(
    query_text:  str,
    query_vector: list[float] | None,
    tags:        list[str] | None,
    severities:  list[str] | None,
    top_k:       int,
) -> dict[str, Any]:
    """Find individual rules (not documents). Returns nested `inner_hits` so
    the caller can see exactly which rule matched."""
    must: list[dict] = []
    if query_text:
        must.append({
            "multi_match": {
                "query": query_text,
                "fields": ["rules.title^2", "rules.requirement", "rules.verification_method"],
            }
        })
    if tags:
        must.append({"terms": {"rules.tag": tags}})
    if severities:
        must.append({"terms": {"rules.severity": severities}})

    return {
        "size": top_k,
        "_source": ["doc_id", "source", "title", "source_url"],
        "query": {
            "nested": {
                "path": "rules",
                "query": {"bool": {"must": must or [{"match_all": {}}]}},
                "inner_hits": {
                    "size": 3,
                    "_source": [
                        "rule_id", "tag", "title", "requirement",
                        "verification_method", "severity",
                        "positive_examples", "negative_examples",
                    ],
                },
            }
        },
    }
```

---

## 6. Pydantic-модели (`app/tools/schemas.py`)

Что инструменты возвращают агенту. Чёткие схемы помогают LLM понять
структуру и не галлюцинировать поля.

```python
"""Schemas for tool return values. The @tool wrappers serialize these to
JSON strings before passing to the LLM."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Source = Literal["cbu", "lex", "eurlex"]
Severity = Literal["critical", "high", "medium", "low"]


class RuleHit(BaseModel):
    rule_id:             str
    tag:                 Optional[str] = None
    title:               Optional[str] = None
    requirement:         str
    verification_method: Optional[str] = None
    severity:            Severity = "medium"
    positive_examples:   list[str] = []
    negative_examples:   list[str] = []


class DocHit(BaseModel):
    doc_id:      str
    source:      Source
    category:    Optional[str] = None
    language:    Optional[str] = None
    title:       str
    source_url:  str
    has_rules:   bool = False
    rules_count: int  = 0
    tags:        list[str] = []
    severities:  list[str] = []
    score:       float = 0.0
    highlights:  list[str] = Field(default_factory=list)
    rules:       list[RuleHit] = Field(default_factory=list)  # only filled by get_document


class RuleSearchHit(BaseModel):
    """A nested-rule match with its parent document context."""
    doc_id:     str
    source:     Source
    doc_title:  str
    source_url: str
    rule:       RuleHit
    score:      float


class TagCount(BaseModel):
    tag:   str
    count: int


class QuoteVerification(BaseModel):
    found:    bool
    in_field: Optional[Literal["full_text", "rule.requirement", "rule.title"]] = None
    snippet:  Optional[str] = None
```

---

## 7. Tools (`app/tools/search.py`)

Это **то, что регистрируется в LangGraph агенте**. Каждая функция:

- помечена `@tool` (LangChain автоматически собирает её схему из аннотаций)
- async, чтобы `httpx`/`elasticsearch.AsyncElasticsearch` не блокировали event loop
- возвращает **JSON-строку** — это формат, который LLM понимает лучше всего
- описание в docstring → описание tool'а для LLM. Пиши его как для джуниора-юриста, читающего твою документацию

```python
"""Tools exposed to the LangGraph agent. Every callable here is registered
with `agent_executor.tools = [search_regulations, search_rules, ...]`."""

from __future__ import annotations

import json
from typing import Optional

from langchain_core.tools import tool

from ._es import EMBEDDING_DIMS, embed, get_es
from ._queries import build_hybrid_query, build_nested_rule_query
from .schemas import DocHit, RuleHit, RuleSearchHit, TagCount, QuoteVerification

ES_INDEX = "regtech-docs"     # читаем из os.environ.get('ES_INDEX', ...) на старте процесса


# ── 1. Hybrid search ───────────────────────────────────────────────────────

@tool
async def search_regulations(
    query: str,
    tags:       Optional[list[str]] = None,
    severities: Optional[list[str]] = None,
    sources:    Optional[list[str]] = None,
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
        JSON-строка с массивом DocHit. У каждого DocHit:
            doc_id, source, title, source_url, score, tags, severities,
            highlights[].
        Поле `rules` пустое (документы тяжелые) — если нужны полные
        правила одного документа, вызывай потом `get_document(doc_id)`.
    """
    top_k = max(1, min(top_k, 20))
    query_vec = await embed(query)
    assert len(query_vec) == EMBEDDING_DIMS, \
        f"embed dim {len(query_vec)} != mapping dim {EMBEDDING_DIMS}"

    body = build_hybrid_query(
        query_text=query, query_vector=query_vec,
        tags=tags, severities=severities, sources=sources, top_k=top_k,
    )
    resp = await get_es().search(index=ES_INDEX, body=body)

    hits: list[DocHit] = []
    for h in resp["hits"]["hits"]:
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
    tags:       Optional[list[str]] = None,
    severities: Optional[list[str]] = None,
    top_k:      int = 10,
) -> str:
    """Поиск конкретных правил (а не документов) по тексту требования.

    Используй когда нужны атомарные комплаенс-требования — например,
    собрать чеклист или найти именно те правила, которые применимы к
    конкретной фиче. Каждый результат — это одно правило с его
    `requirement`, `verification_method` и ссылкой на родительский
    документ.

    Args:
        query: Описание требования. Примеры:
            "согласие на обработку перс. данных",
            "уведомление регулятора при инциденте".
        tags, severities: фильтры, как в `search_regulations`.
        top_k: Сколько правил вернуть. Дефолт 10, максимум 30.

    Returns:
        JSON-строка с массивом RuleSearchHit. У каждого: doc_id, source,
        doc_title, source_url, score, rule { rule_id, tag, title,
        requirement, verification_method, severity, positive_examples,
        negative_examples }.
    """
    top_k = max(1, min(top_k, 30))
    body = build_nested_rule_query(
        query_text=query, query_vector=None,
        tags=tags, severities=severities, top_k=top_k,
    )
    resp = await get_es().search(index=ES_INDEX, body=body)

    out: list[RuleSearchHit] = []
    for h in resp["hits"]["hits"]:
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
                    rule_id             = r["rule_id"],
                    tag                 = r.get("tag"),
                    title               = r.get("title"),
                    requirement         = r["requirement"],
                    verification_method = r.get("verification_method"),
                    severity            = r.get("severity") or "medium",
                    positive_examples   = r.get("positive_examples") or [],
                    negative_examples   = r.get("negative_examples") or [],
                ),
                score = float(h.get("_score") or 0.0),
            ))

    return json.dumps([h.model_dump() for h in out], ensure_ascii=False)


# ── 3. Fetch one document ──────────────────────────────────────────────────

@tool
async def get_document(doc_id: str, include_full_text: bool = False) -> str:
    """Загрузить один документ целиком — со всеми его правилами и опционально
    полным текстом. Используй, когда `search_regulations` уже вернул
    интересный `doc_id` и ты хочешь увидеть все его правила или достать
    конкретную цитату.

    Args:
        doc_id: Идентификатор документа. Примеры:
            "eurlex-32016R0679" (GDPR),
            "cbu-3315-2135465", "lex-7218671".
        include_full_text: Включать ли полный markdown. Дефолт False —
            на длинных EU-регламентах это сотни KB. Ставь True только
            если хочешь искать substring/цитировать.

    Returns:
        JSON-строка с DocHit, у которого заполнено `rules[]` и (по
        запросу) `full_text`. Если документ не найден — возвращает
        строку `{"error": "not_found", "doc_id": "..."}`.
    """
    fields = [
        "doc_id", "source", "category", "language", "title",
        "source_url", "has_rules", "rules_count", "tags", "severities",
        "rules",
    ]
    if include_full_text:
        fields.append("full_text")

    try:
        resp = await get_es().get(index=ES_INDEX, id=doc_id, source=fields)
    except Exception as exc:
        if "not_found" in str(exc).lower() or "404" in str(exc):
            return json.dumps({"error": "not_found", "doc_id": doc_id})
        raise

    src = resp["_source"]
    rules = [
        RuleHit(
            rule_id             = r["rule_id"],
            tag                 = r.get("tag"),
            title               = r.get("title"),
            requirement         = r["requirement"],
            verification_method = r.get("verification_method"),
            severity            = r.get("severity") or "medium",
            positive_examples   = r.get("positive_examples") or [],
            negative_examples   = r.get("negative_examples") or [],
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
    """Перечислить все теги, которые реально присутствуют в индексе, с
    числом документов на каждый тег. Используй на этапе генерации тегов
    запроса (Pipeline шаг 2), чтобы знать, какие фильтры имеет смысл
    передавать в `search_regulations` / `search_rules`.

    Returns:
        JSON-строка с массивом TagCount: [{tag, count}, ...]. Отсортировано
        по убыванию count.
    """
    resp = await get_es().search(
        index=ES_INDEX,
        size=0,
        aggs={"tags": {"terms": {"field": "tags", "size": 50}}},
    )
    buckets = (resp.get("aggregations") or {}).get("tags", {}).get("buckets", [])
    out = [TagCount(tag=b["key"], count=int(b["doc_count"])) for b in buckets]
    return json.dumps([t.model_dump() for t in out], ensure_ascii=False)


# ── 5. Anti-hallucination quote check ──────────────────────────────────────

@tool
async def verify_quote(doc_id: str, quote: str) -> str:
    """Проверить, что цитата действительно встречается в указанном документе
    (либо в `full_text`, либо в `requirement`/`title` одного из правил).

    **Обязательно** вызывай перед тем как включить любую цитату в финальный
    ответ. Если `found=false` — цитата сгенерирована, использовать
    нельзя, выбирай другую или отбрасывай этот риск.

    Args:
        doc_id: Документ, из которого якобы взята цитата.
        quote:  Точный текст цитаты (минимум 20 символов).

    Returns:
        JSON-строка QuoteVerification: { found, in_field, snippet }.
        Если found=true, snippet содержит окрестность найденного фрагмента
        для отображения в UI.
    """
    q = (quote or "").strip()
    if len(q) < 20:
        return json.dumps({"found": False, "in_field": None, "snippet": None,
                           "reason": "quote_too_short"})

    try:
        resp = await get_es().get(
            index=ES_INDEX, id=doc_id,
            source=["full_text", "rules"],
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
```

---

## 8. Регистрация в LangGraph

В файле `app/agent/graph.py`:

```python
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from app.tools.search import (
    search_regulations,
    search_rules,
    get_document,
    list_available_tags,
    verify_quote,
)

TOOLS = [
    search_regulations,
    search_rules,
    get_document,
    list_available_tags,
    verify_quote,
]

SYSTEM_PROMPT = """Ты — ассистент-комплаенс. По описанию продуктовой фичи
определяешь регуляторные риски и формируешь чеклист.

ПРАВИЛА:
1. Любой риск, который ты называешь в ответе, ДОЛЖЕН подкрепляться
   правилом из инструмента `search_regulations` или `search_rules`. Без
   retrieval — никаких рисков.
2. Любая цитата, которую ты включаешь в обоснование, ДОЛЖНА быть
   проверена через `verify_quote(doc_id, quote)` ДО включения в ответ.
   Если found=false — выбери другую цитату или отбрось риск.
3. Используй `list_available_tags()` на старте, чтобы знать какие
   фильтры существуют.
4. На каждый detected risk должен быть хотя бы один checklist item.
5. Финальный ответ — только в формате Structured Output (см. схему).
"""

def build_agent():
    llm = ChatAnthropic(
        model="claude-3-5-sonnet-20241022",
        temperature=0,
        max_tokens=4096,
    )
    return create_react_agent(llm, TOOLS, state_modifier=SYSTEM_PROMPT)
```

---

## 9. Типичные сценарии вызова

### Сценарий 1: "Добавить кнопку шаринга контактов" (data privacy)

```text
LLM: Думает: фича про обмен контактами → возможно personal_data.
     Сначала проверю таксономию тегов.
TOOL: list_available_tags()
     → [{"tag":"integration","count":358},{"tag":"personal_data","count":132},…]

LLM: personal_data есть. Делаю hybrid поиск с фильтром.
TOOL: search_regulations(
        query="передача контактных данных пользователя третьим лицам",
        tags=["personal_data"],
        top_k=5
      )
     → [DocHit(doc_id="eurlex-32016R0679", title="GDPR", score=12.4, …),
        DocHit(doc_id="cbu-3315-…", title="О персональных данных", score=9.1, …)]

LLM: Достаю правила GDPR'а.
TOOL: get_document(doc_id="eurlex-32016R0679")
     → DocHit с rules=[RuleHit(rule_id="R-PDP-001", tag="personal_data",
                                requirement="…lawful basis…", severity="critical"), …]

LLM: Беру цитату из R-PDP-001 для пруфа. Проверяю.
TOOL: verify_quote(doc_id="eurlex-32016R0679",
                   quote="processing shall be lawful only if and to the extent…")
     → {"found": true, "in_field": "rule.requirement", "snippet": "…"}

LLM: Возвращает финальный JSON.
```

### Сценарий 2: "Внедрить биометрическую аутентификацию" (KYC + security)

```text
TOOL: list_available_tags()
TOOL: search_rules(
        query="биометрические данные аутентификация",
        tags=["personal_data", "kyc", "cybersecurity"],
        severities=["critical", "high"],
        top_k=15
      )
     → 15 атомарных правил из GDPR (биометрия = sensitive data),
       и из CBU (требования к 2FA для платежных систем).
TOOL: get_document(...) — для каждого правила, чтобы взять source_url
TOOL: verify_quote(...) — для каждой цитаты
```

### Сценарий 3: "Добавить аналитическую отчётность" (низкий приоритет)

```text
TOOL: search_regulations(query="внутренняя аналитическая отчётность", top_k=3)
     → пусто или очень низкие score'ы

LLM: Низкий confidence → возвращает "регуляторных рисков не обнаружено,
     рекомендуется консультация с DPO если данные включают персональные".
```

---

## 10. Производительность и тайминги

Замеры на текущей инсталляции (3 877 документов, MinIO/Postgres/ES на
одном хосте):

| операция                                       | latency (медиана) |
|------------------------------------------------|-------------------|
| `embed(query)` — OpenAI text-embedding-3-small | 80–150 мс         |
| `search_regulations()` — hybrid, top_k=5       | 30–50 мс (ES)     |
| `search_rules()` — nested + inner_hits         | 40–80 мс          |
| `get_document()`                               | 5–15 мс           |
| `list_available_tags()` — aggregation only     | 10–20 мс          |
| `verify_quote()`                               | 5–10 мс           |

Полный pipeline агента (Claude Sonnet, ~3 LLM-calls + ~5 tool-calls)
укладывается в **~3.5 сек** для большинства запросов.

---

## 11. Ошибки и деградация

- **ES недоступен:** `AsyncElasticsearch` сам ретраит дважды
  (`max_retries=2`); если упало — tool возвращает строку с `error`.
  Агент должен уметь сказать "не могу проверить, попробуйте позже"
  вместо галлюцинаций.
- **Эмбеддинг не получился (rate-limit / 5xx OpenRouter):** обернуть
  `embed()` в `tenacity.retry(stop=stop_after_attempt(3),
  wait=wait_exponential(multiplier=1, max=4))`.
- **Пустой результат поиска:** возвращай `"[]"` — это валидный JSON,
  LLM прочитает как "ничего не нашлось" и должен честно об этом
  сказать.
- **LLM придумал несуществующий `doc_id`:** ловится в `get_document` /
  `verify_quote` → `{"error":"not_found", ...}`. Сценарий на этапе
  Final Validator — провалить ответ и пойти на второй круг.

---

## 12. Связь с ETL: что обязательно держать синхронизированным

Эти три значения **должны быть одинаковы** в `.env` ETL и `.env`
ai-core, иначе индекс становится непригоден для агента:

```bash
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMS=1536
LLM_BASE_URL=https://openrouter.ai/api/v1
```

Если решите заменить модель эмбеддинга:
1. Поменяйте `EMBEDDING_MODEL` и `EMBEDDING_DIMS` в **обоих** `.env`.
2. На ETL стороне: `python es_indexer.py --recreate-index --backfill`
   — это пересоздаст индекс с новыми размерностями (~15 мин на 4K
   документов).
3. Перезапустите ai-core.

`ES_URL` / `ES_INDEX` можно различать — оба сервиса должны попадать в
один и тот же кластер с одним и тем же именем индекса, но сетевые
endpoint'ы могут отличаться (см. секцию 2 про docker vs host).
