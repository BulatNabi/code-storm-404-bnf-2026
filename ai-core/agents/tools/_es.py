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
            api_key=os.environ.get("LLM_API_KEY", "dummy"),
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
