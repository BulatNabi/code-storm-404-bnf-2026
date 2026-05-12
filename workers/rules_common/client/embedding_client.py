"""
Embedding client — OpenAI-compatible Embeddings API. Mirrors LLMClient.

Designed for OpenRouter / OpenAI / any provider speaking the OpenAI protocol.
Used by `es_indexer.py` to embed document summaries before indexing.

Env (preferred):
    EMBEDDING_BASE_URL   — base URL of the embeddings endpoint
                           (default: same as LLM_BASE_URL, i.e. OpenRouter)
    EMBEDDING_API_KEY    — API key (default: same as LLM_API_KEY)
    EMBEDDING_MODEL      — model slug (default: openai/text-embedding-3-small,
                           1536 dims, OpenRouter-routed to OpenAI)
    EMBEDDING_DIMS       — expected dim count (default: 1536 for 3-small)

Legacy fallbacks: LLM_BASE_URL / LLM_API_KEY are reused so a single
OpenRouter key powers both chat and embeddings.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Iterable

from openai import OpenAI
from openai import APIError, RateLimitError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL    = "openai/text-embedding-3-small"
DEFAULT_DIMS     = 1536

# OpenAI Embeddings API caps a single request at 8191 tokens per text and
# ~100-200 texts per batch. We truncate by character count (~3 chars/token
# for Cyrillic, ~4 for English) to stay well clear of the per-text cap.
MAX_INPUT_CHARS = int(os.getenv("EMBEDDING_MAX_INPUT_CHARS", "24000"))
MAX_BATCH       = int(os.getenv("EMBEDDING_BATCH_SIZE",      "64"))


class EmbeddingClient:
    """Synchronous OpenAI-compatible embeddings client."""

    def __init__(self, api_key: str, model: str, base_url: str, dims: int):
        self.api_key = api_key
        self.model   = model
        self.base_url = base_url
        self.dims    = dims
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)

    @classmethod
    def from_env(cls) -> "EmbeddingClient":
        base_url = (
            os.environ.get("EMBEDDING_BASE_URL")
            or os.environ.get("LLM_BASE_URL")
            or DEFAULT_BASE_URL
        )
        api_key = (
            os.environ.get("EMBEDDING_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
        )
        model = os.environ.get("EMBEDDING_MODEL", DEFAULT_MODEL)
        dims  = int(os.environ.get("EMBEDDING_DIMS", str(DEFAULT_DIMS)))

        if not api_key:
            raise RuntimeError(
                "EMBEDDING_API_KEY (or LLM_API_KEY) is not set. "
                "Configure EMBEDDING_BASE_URL/EMBEDDING_API_KEY/EMBEDDING_MODEL."
            )

        logger.info("EmbeddingClient: provider=%s model=%s dims=%d",
                    base_url, model, dims)
        return cls(api_key=api_key, model=model, base_url=base_url, dims=dims)

    def embed_batch(self, texts: list[str], retries: int = 3) -> list[list[float]]:
        """Embed a list of texts. Splits large lists into MAX_BATCH-sized
        requests and truncates each text to MAX_INPUT_CHARS. Returns one
        vector per input, in the same order."""
        if not texts:
            return []
        out: list[list[float]] = []
        for chunk in _chunked(texts, MAX_BATCH):
            payload = [_truncate(t) for t in chunk]
            out.extend(self._embed_one_batch(payload, retries=retries))
        return out

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    # ── internals ───────────────────────────────────────────────────────────

    def _embed_one_batch(self, texts: list[str], retries: int) -> list[list[float]]:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                resp = self._client.embeddings.create(model=self.model, input=texts)
                # Order by index — some providers don't guarantee order in the response.
                items = sorted(resp.data, key=lambda d: d.index)
                vectors = [list(d.embedding) for d in items]
                if any(len(v) != self.dims for v in vectors):
                    raise RuntimeError(
                        f"embedding dim mismatch: model={self.model} expected={self.dims} "
                        f"got={len(vectors[0]) if vectors else 0}. "
                        f"Set EMBEDDING_DIMS to match, or change the index mapping."
                    )
                return vectors
            except RateLimitError as exc:
                last_exc = exc
                sleep_s = 2 ** attempt
                logger.warning("embed: rate-limited (attempt %d/%d), sleeping %ds",
                               attempt, retries, sleep_s)
                time.sleep(sleep_s)
            except APIError as exc:
                last_exc = exc
                sleep_s = 2 ** attempt
                logger.warning("embed: API error (attempt %d/%d): %s — sleeping %ds",
                               attempt, retries, exc, sleep_s)
                time.sleep(sleep_s)
        raise RuntimeError(f"embed: failed after {retries} retries: {last_exc}")


def _truncate(text: str) -> str:
    if len(text) <= MAX_INPUT_CHARS:
        return text
    return text[:MAX_INPUT_CHARS]


def _chunked(seq: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
