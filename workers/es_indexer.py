"""
ES Indexer Worker — indexes regulatory documents + their extracted rules into
an Elasticsearch index (`regtech-docs`) for the downstream LangChain agent.

Two modes:
    python es_indexer.py --backfill                 # one-shot: index every
                                                    # doc currently in Postgres
    python es_indexer.py                            # stream: Kafka consumer
                                                    # on reg.md + reg.rules
    python es_indexer.py --once                     # smoke test: drain
                                                    # buffered Kafka events
                                                    # and exit
    python es_indexer.py --backfill --limit 5       # backfill first N docs
                                                    # (smoke test)
    python es_indexer.py --recreate-index           # DROP + CREATE the index
                                                    # (destructive; use with care)

ES document shape — one ES doc per regulatory document. `rules` is a nested
field so the agent can filter "rules where severity=critical and
tag=personal_data" inside a single matched document. A flat `tags` /
`severities` array at top level enables faceted aggregations without nested
aggs.

Embeddings are computed over a `summary` field (title + rules-concat, or
title + MD prefix when the doc has no extracted rules yet). One vector per
document — kept small because the agent needs sub-second retrieval.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3
import psycopg2
import psycopg2.extras
from botocore.config import Config as BotoConfig
from elasticsearch import Elasticsearch, helpers
from elasticsearch.exceptions import NotFoundError
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import CommitFailedError, KafkaError

sys.path.insert(0, os.path.dirname(__file__))
import config
from rules_common.client.embedding_client import EmbeddingClient

logger = logging.getLogger("es_indexer")

INDEXER_VERSION = "es-1"

ES_URL                    = os.getenv("ES_URL",                    "http://localhost:9200")
ES_INDEX                  = os.getenv("ES_INDEX",                  "regtech-docs")
ES_BULK_SIZE              = int(os.getenv("ES_BULK_SIZE",          "50"))
ES_INDEXER_CONSUMER_GROUP = os.getenv("ES_INDEXER_CONSUMER_GROUP", "es-indexer-v1")
EMBEDDING_DIMS            = int(os.getenv("EMBEDDING_DIMS",        "1536"))
MD_S3_BUCKET              = os.getenv("MD_S3_BUCKET",              "regtech-md")
MD_TOPIC                  = os.getenv("MD_TOPIC",                  "reg.md")
RULES_TOPIC               = os.getenv("RULES_TOPIC",               "reg.rules")
# Output topic — one `document.indexed` event per successfully indexed doc.
# Lets downstream (UI / agent / notifier) react to "doc is now searchable".
INDEXED_TOPIC             = os.getenv("INDEXED_TOPIC",             "reg.indexed")
POLL_TIMEOUT_MS           = int(os.getenv("ES_POLL_TIMEOUT_MS",    "10000"))

# Summary = title + (rules concat | md prefix). Trimmed before embedding —
# the EmbeddingClient also caps at MAX_INPUT_CHARS but doing it here keeps
# the indexed `summary` field readable.
SUMMARY_MAX_CHARS = int(os.getenv("ES_SUMMARY_MAX_CHARS", "8000"))
# Cap the indexed full_text to keep _source small enough to be cheap to
# return for highlighting. 200KB covers ~all our EUR-Lex docs.
FULL_TEXT_MAX_CHARS = int(os.getenv("ES_FULL_TEXT_MAX_CHARS", "200000"))


def _index_mapping(dims: int) -> dict:
    """ES mapping for `regtech-docs`. Dense_vector dims must match the
    embedding model — if you change EMBEDDING_MODEL, also bump
    EMBEDDING_DIMS and re-create the index."""
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "5s",
        },
        "mappings": {
            "properties": {
                "doc_id":        {"type": "keyword"},
                "source":        {"type": "keyword"},
                "category":      {"type": "keyword"},
                "language":      {"type": "keyword"},
                "title":         {"type": "text"},
                "source_url":    {"type": "keyword"},
                "discovered_at": {"type": "date"},
                "full_text":     {"type": "text"},
                "summary":       {"type": "text"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": dims,
                    "index": True,
                    "similarity": "cosine",
                },
                "has_rules":   {"type": "boolean"},
                "rules_count": {"type": "integer"},
                # Flat arrays for cheap top-level facet aggs without nested aggs.
                "tags":       {"type": "keyword"},
                "severities": {"type": "keyword"},
                "rules": {
                    "type": "nested",
                    "properties": {
                        "rule_id":             {"type": "keyword"},
                        "tag":                 {"type": "keyword"},
                        "title":               {"type": "text"},
                        "requirement":         {"type": "text"},
                        "verification_method": {"type": "text"},
                        "severity":            {"type": "keyword"},
                        "positive_examples":   {"type": "text"},
                        "negative_examples":   {"type": "text"},
                    },
                },
            }
        },
    }


# Topic → "what changed" mapping. Both events trigger a full re-index of
# the document (cheap, idempotent — ES upsert by doc_id).
STREAM_TOPICS = [MD_TOPIC, RULES_TOPIC]


@dataclass
class DocRow:
    doc_id:        str
    source:        str
    category:      str | None
    language:      str | None
    title:         str
    source_url:    str
    discovered_at: Any           # datetime
    md_s3_key:     str
    rules:         list[dict]    # may be empty


@dataclass
class IndexedEvent:
    event_type:  str
    doc_id:      str
    source:      str
    es_index:    str
    has_rules:   bool
    rules_count: int
    indexed_at:  str


class ESIndexer:
    def __init__(self, publish: bool = True) -> None:
        self.es = Elasticsearch(ES_URL, request_timeout=60)
        self.s3 = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            config=BotoConfig(retries={"max_attempts": 3}, signature_version="s3v4"),
        )
        self.conn = psycopg2.connect(
            host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
            user=config.DB_USER, password=config.DB_PASSWORD,
        )
        self.conn.autocommit = True
        # Embedding client built lazily — avoids touching OpenRouter at
        # import time, which makes `--recreate-index` and `--help` work
        # even when LLM_API_KEY isn't loaded yet.
        self._embed: EmbeddingClient | None = None

        # Optional KafkaProducer for the outbound reg.indexed signal. Off
        # in --backfill mode by default (would flood the topic with 1500
        # events on bootstrap); on in stream mode.
        self.publish = publish
        self.producer: KafkaProducer | None = None
        if self.publish:
            self.producer = KafkaProducer(
                bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS.split(","),
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                acks="all", retries=3,
            )

    # ── public API ──────────────────────────────────────────────────────────

    def ensure_index(self, recreate: bool = False) -> None:
        exists = self.es.indices.exists(index=ES_INDEX)
        if exists and recreate:
            logger.warning("recreate-index: dropping existing %s", ES_INDEX)
            self.es.indices.delete(index=ES_INDEX)
            exists = False
        if not exists:
            logger.info("creating index %s (dims=%d)", ES_INDEX, EMBEDDING_DIMS)
            self.es.indices.create(index=ES_INDEX, body=_index_mapping(EMBEDDING_DIMS))
        else:
            logger.info("index %s already exists — leaving mapping untouched", ES_INDEX)

    def backfill(self, limit: int | None = None) -> int:
        """One-shot: iterate every doc with successful MD and index it.
        Streams in batches of ES_BULK_SIZE to keep memory bounded."""
        total = self._count_eligible()
        if limit:
            total = min(total, limit)
        logger.info("backfill: %d docs eligible", total)

        ok, errs = 0, 0
        for batch in self._iter_doc_rows(limit=limit, batch_size=ES_BULK_SIZE):
            docs = self._build_docs(batch)
            if not docs:
                continue
            b_ok, b_err = self._bulk_index(docs)
            self._publish_indexed(docs)
            ok  += b_ok
            errs += b_err
            logger.info("backfill: progress %d/%d ok=%d err=%d",
                        ok + errs, total, ok, errs)

        self.es.indices.refresh(index=ES_INDEX)
        self._flush_producer()
        logger.info("backfill done: indexed=%d errors=%d", ok, errs)
        return ok

    def run_stream(self, once: bool = False) -> int:
        """Kafka consumer on reg.md + reg.rules. Each event triggers a full
        re-index of the doc."""
        consumer = KafkaConsumer(
            *STREAM_TOPICS,
            bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS.split(","),
            group_id=ES_INDEXER_CONSUMER_GROUP,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            consumer_timeout_ms=POLL_TIMEOUT_MS if once else float("inf"),
            max_poll_records=ES_BULK_SIZE,
            max_poll_interval_ms=int(os.getenv("ES_MAX_POLL_INTERVAL_MS", "900000")),
            session_timeout_ms=int(os.getenv("ES_SESSION_TIMEOUT_MS",   "60000")),
            heartbeat_interval_ms=int(os.getenv("ES_HEARTBEAT_MS",      "10000")),
            request_timeout_ms=int(os.getenv("ES_REQUEST_TIMEOUT_MS",   "65000")),
        )
        logger.info("subscribed to %s (group=%s) → index=%s",
                    STREAM_TOPICS, ES_INDEXER_CONSUMER_GROUP, ES_INDEX)

        processed = 0
        try:
            # Drain in batches: collect doc_ids from poll, dedup, fetch +
            # index together.
            batch_ids: list[str] = []
            for msg in consumer:
                doc_id = (msg.value or {}).get("doc_id")
                if doc_id:
                    batch_ids.append(doc_id)
                if len(batch_ids) >= ES_BULK_SIZE:
                    processed += self._reindex_doc_ids(batch_ids)
                    self._commit(consumer)
                    batch_ids = []
            if batch_ids:
                processed += self._reindex_doc_ids(batch_ids)
                self._commit(consumer)
        except StopIteration:
            pass
        finally:
            self._flush_producer()
            consumer.close()
        logger.info("stream done. processed=%d", processed)
        return processed

    # ── internals ───────────────────────────────────────────────────────────

    def _embed_client(self) -> EmbeddingClient:
        if self._embed is None:
            self._embed = EmbeddingClient.from_env()
            if self._embed.dims != EMBEDDING_DIMS:
                raise RuntimeError(
                    f"EMBEDDING_DIMS={EMBEDDING_DIMS} but client reports "
                    f"dims={self._embed.dims}. Re-create the index with the "
                    f"right dim count, or pin EMBEDDING_DIMS to match."
                )
        return self._embed

    def _reindex_doc_ids(self, doc_ids: list[str]) -> int:
        """Re-fetch the docs from PG, build ES bodies, bulk-index."""
        seen: set[str] = set()
        deduped = [d for d in doc_ids if not (d in seen or seen.add(d))]
        rows = self._fetch_doc_rows_by_ids(deduped)
        docs = self._build_docs(rows)
        if not docs:
            return 0
        ok, _ = self._bulk_index(docs)
        self._publish_indexed(docs)
        return ok

    def _build_docs(self, rows: list[DocRow]) -> list[dict]:
        """Fetch MDs, compute embeddings in one batch call, return ES bodies."""
        if not rows:
            return []

        # Fetch all MDs first.
        mds: dict[str, str] = {}
        for r in rows:
            try:
                mds[r.doc_id] = self._download_md(r.md_s3_key)
            except Exception as exc:
                logger.warning("md fetch failed for %s (%s): %s — using empty",
                               r.doc_id, r.md_s3_key, exc)
                mds[r.doc_id] = ""

        # Build summaries (what we embed).
        summaries = [_summary_text(r.title, r.rules, mds[r.doc_id]) for r in rows]
        # One batched embedding call per build_docs invocation (≤ ES_BULK_SIZE).
        vectors = self._embed_client().embed_batch(summaries)

        out: list[dict] = []
        for r, summary, vec in zip(rows, summaries, vectors):
            md_text = mds[r.doc_id]
            tags       = sorted({rl.get("tag")      for rl in r.rules if rl.get("tag")})
            severities = sorted({rl.get("severity") for rl in r.rules if rl.get("severity")})
            body = {
                "doc_id":        r.doc_id,
                "source":        r.source,
                "category":      r.category,
                "language":      r.language,
                "title":         r.title,
                "source_url":    r.source_url,
                "discovered_at": r.discovered_at.isoformat() if r.discovered_at else None,
                "full_text":     md_text[:FULL_TEXT_MAX_CHARS],
                "summary":       summary,
                "embedding":     vec,
                "has_rules":     bool(r.rules),
                "rules_count":   len(r.rules),
                "tags":          tags,
                "severities":    severities,
                "rules":         r.rules,
            }
            out.append(body)
        return out

    def _bulk_index(self, docs: list[dict]) -> tuple[int, int]:
        actions = (
            {
                "_op_type": "index",
                "_index":   ES_INDEX,
                "_id":      doc["doc_id"],
                "_source":  doc,
            }
            for doc in docs
        )
        ok_count, errors = helpers.bulk(
            self.es, actions,
            raise_on_error=False,
            raise_on_exception=False,
            stats_only=False,
        )
        err_count = len(errors)
        if err_count:
            # Log the first error to aid debugging; full list could be huge.
            logger.error("bulk: %d errors. first=%s", err_count, errors[0])
        return ok_count, err_count

    # ── postgres ────────────────────────────────────────────────────────────

    def _count_eligible(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM public.documents d "
                "JOIN public.documents_md m USING (doc_id) "
                "WHERE m.status = 'done'"
            )
            return cur.fetchone()[0]

    def _iter_doc_rows(self, limit: int | None, batch_size: int) -> Iterable[list[DocRow]]:
        """Server-side cursor over (documents ⋈ documents_md ⋈ rules)."""
        cap = "" if limit is None else f"LIMIT {int(limit)}"
        sql = f"""
        SELECT
          d.doc_id, d.source_id, d.category, d.language, d.name, d.source_url,
          d.first_seen_at,
          m.md_s3_key,
          COALESCE(
            json_agg(
              json_build_object(
                'rule_id',             r.rule_id,
                'tag',                 r.tag,
                'title',               r.title,
                'requirement',         r.requirement,
                'verification_method', r.verification_method,
                'positive_examples',   r.positive_examples,
                'negative_examples',   r.negative_examples,
                'severity',            r.severity
              ) ORDER BY r.rule_id
            ) FILTER (WHERE r.rule_id IS NOT NULL),
            '[]'::json
          ) AS rules
        FROM public.documents d
        JOIN public.documents_md m ON m.doc_id = d.doc_id AND m.status = 'done'
        LEFT JOIN public.rules r   ON r.doc_id = d.doc_id
        GROUP BY d.doc_id, m.md_s3_key
        ORDER BY d.doc_id
        {cap}
        """
        # Client-side cursor — psycopg2 named cursors require an explicit
        # transaction, and the connection is in autocommit=True. The full
        # result set is ~1500 rows for our scale, so client-side buffering
        # is fine; we still yield in `batch_size` chunks downstream.
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            buf: list[DocRow] = []
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    buf.append(_row_to_doc(row))
                yield buf
                buf = []

    def _fetch_doc_rows_by_ids(self, doc_ids: list[str]) -> list[DocRow]:
        if not doc_ids:
            return []
        sql = """
        SELECT
          d.doc_id, d.source_id, d.category, d.language, d.name, d.source_url,
          d.first_seen_at,
          m.md_s3_key,
          COALESCE(
            json_agg(
              json_build_object(
                'rule_id',             r.rule_id,
                'tag',                 r.tag,
                'title',               r.title,
                'requirement',         r.requirement,
                'verification_method', r.verification_method,
                'positive_examples',   r.positive_examples,
                'negative_examples',   r.negative_examples,
                'severity',            r.severity
              ) ORDER BY r.rule_id
            ) FILTER (WHERE r.rule_id IS NOT NULL),
            '[]'::json
          ) AS rules
        FROM public.documents d
        JOIN public.documents_md m ON m.doc_id = d.doc_id AND m.status = 'done'
        LEFT JOIN public.rules r   ON r.doc_id = d.doc_id
        WHERE d.doc_id = ANY(%s)
        GROUP BY d.doc_id, m.md_s3_key
        """
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (doc_ids,))
            return [_row_to_doc(row) for row in cur.fetchall()]

    def _download_md(self, s3_key: str) -> str:
        obj = self.s3.get_object(Bucket=MD_S3_BUCKET, Key=s3_key)
        return obj["Body"].read().decode("utf-8", errors="replace")

    def _commit(self, consumer: KafkaConsumer) -> None:
        try:
            consumer.commit()
        except CommitFailedError as exc:
            logger.warning("commit failed (rebalance?): %s", exc)

    def _publish_indexed(self, docs: list[dict]) -> None:
        """Fan out one `document.indexed` event per indexed doc on
        reg.indexed. No-op when the producer is disabled (backfill --no-publish)."""
        if not self.producer:
            return
        for d in docs:
            evt = IndexedEvent(
                event_type  = "document.indexed",
                doc_id      = d["doc_id"],
                source      = d.get("source") or "",
                es_index    = ES_INDEX,
                has_rules   = bool(d.get("has_rules")),
                rules_count = int(d.get("rules_count") or 0),
                indexed_at  = _now_iso(),
            )
            try:
                self.producer.send(INDEXED_TOPIC, value=asdict(evt))
            except KafkaError as exc:
                logger.warning("kafka publish failed for %s: %s", evt.doc_id, exc)

    def _flush_producer(self) -> None:
        if self.producer:
            try:
                self.producer.flush(timeout=10)
            except Exception:
                pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_doc(row: dict) -> DocRow:
    return DocRow(
        doc_id        = row["doc_id"],
        source        = row["source_id"],
        category      = row.get("category"),
        language      = row.get("language"),
        title         = row.get("name") or "",
        source_url    = row.get("source_url") or "",
        discovered_at = row.get("first_seen_at"),
        md_s3_key     = row["md_s3_key"],
        rules         = row.get("rules") or [],
    )


def _summary_text(title: str, rules: list[dict], md_text: str) -> str:
    """The text fed to the embedding model.

    When rules exist they're a much stronger signal than raw MD — they're
    already the LLM-distilled "what this doc requires", which is exactly
    what the agent searches for. When rules are missing (extractor failed
    or hasn't run), fall back to the first chunk of the markdown so the
    doc is still findable.
    """
    title = (title or "").strip()
    if rules:
        rule_lines = []
        for r in rules:
            piece = " — ".join(filter(None, [r.get("title"), r.get("requirement")]))
            tag = r.get("tag")
            if tag:
                piece = f"[{tag}] {piece}"
            rule_lines.append(piece)
        body = " ⏐ ".join(rule_lines)
    else:
        body = (md_text or "").strip()
    summary = f"{title}\n\n{body}" if title else body
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS]
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Elasticsearch indexer worker")
    ap.add_argument("--backfill", action="store_true",
                    help="One-shot: index every doc currently in Postgres and exit")
    ap.add_argument("--once", action="store_true",
                    help="Stream mode: drain currently-buffered Kafka events and exit")
    ap.add_argument("--limit", type=int, default=None,
                    help="In --backfill mode, cap the number of docs (smoke test)")
    ap.add_argument("--recreate-index", action="store_true",
                    help="DROP and CREATE the ES index before running (destructive)")
    ap.add_argument("--no-publish", action="store_true",
                    help="Skip producing reg.indexed events (default: publish on)")
    args = ap.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("kafka").setLevel(logging.WARNING)
    logging.getLogger("elastic_transport").setLevel(logging.WARNING)
    logging.getLogger("elasticsearch").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Publish reg.indexed events by default in both modes. Backfill on
    # first run produces one event per doc which makes the topic visible
    # to operators and to any downstream consumer that wants to react.
    idx = ESIndexer(publish=not args.no_publish)
    idx.ensure_index(recreate=args.recreate_index)

    if args.backfill:
        idx.backfill(limit=args.limit)
    else:
        idx.run_stream(once=args.once)


if __name__ == "__main__":
    main()
