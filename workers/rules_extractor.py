"""
Rules Extractor Worker — consumes document.md.ready events from `reg.md`,
downloads the markdown from MinIO, runs the rules_generator.RuleGenerator
(LLM-based extraction of atomic regulatory rules), and persists results to
Postgres (public.rules + public.rule_extractions), then publishes
`document.rules.ready` on reg.rules for downstream subscribers.

Run:
    python rules_extractor.py             # consume forever
    python rules_extractor.py --once      # drain currently-buffered events and exit

Env:
    YANDEX_CLOUD_API_KEY     — Yandex Cloud LLM API key (required)
    YANDEX_CLOUD_FOLDER      — Yandex Cloud folder ID (required)
    RULES_TOPIC              — output Kafka topic (default reg.rules)
    RULES_CONSUMER_GROUP     — Kafka group (default rules-extractor)
    RULES_MAX_TOKENS         — chunking max tokens (default 50000)
    RULES_CHUNK_SIZE         — chunk size in tokens (default 40000)
    RULES_RESPONSE_MAX       — LLM response max tokens (default 8000)
    MD_S3_BUCKET             — MinIO bucket holding .md files (default regtech-md)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
import psycopg2
import psycopg2.extras
from botocore.config import Config as BotoConfig
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import CommitFailedError, KafkaError

sys.path.insert(0, os.path.dirname(__file__))
import config
from rules_common.client.llm_client import LLMClient
from rules_generator import RuleGenerator

logger = logging.getLogger("rules_extractor")

EXTRACTOR_VERSION = "rg-1"

MD_TOPIC             = os.getenv("MD_TOPIC",             "reg.md")
RULES_TOPIC          = os.getenv("RULES_TOPIC",          "reg.rules")
RULES_CONSUMER_GROUP = os.getenv("RULES_CONSUMER_GROUP", "rules-extractor")
MD_S3_BUCKET         = os.getenv("MD_S3_BUCKET",         "regtech-md")
POLL_TIMEOUT_MS      = int(os.getenv("RULES_POLL_TIMEOUT_MS", "10000"))

# Parallel LLM calls per process. Each worker thread gets its own LLM
# request. Tune up if your provider/quota allows.
RULES_PARALLELISM    = int(os.getenv("RULES_PARALLELISM",    "3"))
# How many messages to drain from Kafka per batch before waiting for the
# batch to finish. Should be >= RULES_PARALLELISM so the pool stays full.
RULES_BATCH_SIZE     = int(os.getenv("RULES_BATCH_SIZE",     str(RULES_PARALLELISM)))
# Optional comma-separated source filter. Empty/unset → process every
# source. Example: RULES_SOURCES=cbu,eurlex to skip Lex events.
RULES_SOURCES        = {s.strip() for s in os.getenv("RULES_SOURCES", "").split(",") if s.strip()}


@dataclass
class RulesEvent:
    event_type:    str
    doc_id:        str
    source:        str
    rules_count:   int
    strategy:      str
    extracted_at:  str


class RulesExtractor:
    def __init__(self) -> None:
        self.s3 = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            config=BotoConfig(retries={"max_attempts": 3}, signature_version="s3v4"),
        )
        # `self.conn` is used by the main thread (e.g. _already_extracted in
        # the smoke-test path). Worker threads use `_db()` which returns a
        # thread-local connection so we don't share cursors across threads.
        self.conn = psycopg2.connect(
            host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
            user=config.DB_USER, password=config.DB_PASSWORD,
        )
        self.conn.autocommit = True

        self.producer = KafkaProducer(
            bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS.split(","),
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
            acks="all", retries=3,
        )

        # Postgres connections aren't thread-safe across cursors, so each
        # worker thread gets its own. Built lazily inside the thread.
        self._tls = threading.local()

        # LLM client + generator initialised lazily on first message.
        self._generator: RuleGenerator | None = None
        self._generator_lock = threading.Lock()

    # ── public API ──────────────────────────────────────────────────────────

    def run(self, once: bool = False) -> int:
        consumer = KafkaConsumer(
            MD_TOPIC,
            bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS.split(","),
            group_id=RULES_CONSUMER_GROUP,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            consumer_timeout_ms=POLL_TIMEOUT_MS if once else float("inf"),
            # Pull a full batch so the threadpool stays saturated.
            max_poll_records=max(RULES_BATCH_SIZE, RULES_PARALLELISM),
            max_poll_interval_ms=int(os.getenv("RULES_MAX_POLL_INTERVAL_MS", "1800000")),
            session_timeout_ms=int(os.getenv("RULES_SESSION_TIMEOUT_MS",   "60000")),
            heartbeat_interval_ms=int(os.getenv("RULES_HEARTBEAT_MS",      "10000")),
            request_timeout_ms=int(os.getenv("RULES_REQUEST_TIMEOUT_MS",   "65000")),
        )
        logger.info("subscribed to %s (group=%s) → bucket=%s out=%s | "
                    "parallelism=%d  sources=%s",
                    MD_TOPIC, RULES_CONSUMER_GROUP, MD_S3_BUCKET, RULES_TOPIC,
                    RULES_PARALLELISM,
                    sorted(RULES_SOURCES) or "ALL")

        processed = 0
        pool = ThreadPoolExecutor(
            max_workers=RULES_PARALLELISM,
            thread_name_prefix="rules-",
        )
        try:
            # Drain batches: poll the iterator, group messages into a batch,
            # submit all to the pool, wait for them, commit, repeat. Commit
            # only after the full batch finishes so we never advance the
            # offset past an in-flight or failed message.
            batch: list = []
            for msg in consumer:
                batch.append(msg)
                if len(batch) < RULES_BATCH_SIZE:
                    continue
                processed += self._process_batch(batch, pool)
                self._commit(consumer)
                batch = []
            # Flush whatever's left when the iterator exits (--once mode or shutdown)
            if batch:
                processed += self._process_batch(batch, pool)
                self._commit(consumer)
        except StopIteration:
            pass
        finally:
            pool.shutdown(wait=True)
            try:
                self.producer.flush(timeout=10)
            except Exception:
                pass
            consumer.close()
        logger.info("done. processed=%d", processed)
        return processed

    def _process_batch(self, batch: list, pool: ThreadPoolExecutor) -> int:
        """Submit every msg.value to the threadpool and wait for the whole batch
        to finish. Returns count of successful conversions."""
        futures = {
            pool.submit(self._handle_safe, msg.value): msg
            for msg in batch
        }
        ok = 0
        for fut in as_completed(futures):
            msg = futures[fut]
            try:
                if fut.result():
                    ok += 1
            except Exception as exc:
                logger.error("worker thread crashed on %s/%d/%d: %s",
                             msg.topic, msg.partition, msg.offset, exc, exc_info=True)
        return ok

    def _handle_safe(self, event: dict) -> bool:
        """Wrapper around _handle that never raises — exceptions get logged
        and recorded as `rule_extractions.status='error'`."""
        try:
            return self._handle(event)
        except Exception as exc:
            doc_id = event.get("doc_id", "?")
            logger.error("error handling %s: %s", doc_id, exc, exc_info=True)
            if doc_id and doc_id != "?":
                try:
                    self._record_error(doc_id, str(exc))
                except Exception:
                    pass
            return False

    def _commit(self, consumer: KafkaConsumer) -> None:
        try:
            consumer.commit()
        except CommitFailedError as exc:
            logger.warning("commit failed (rebalance?), retry on next poll: %s", exc)

    # ── internals ───────────────────────────────────────────────────────────

    def _handle(self, event: dict) -> bool:
        doc_id    = event.get("doc_id")
        source    = event.get("source")
        md_s3_key = event.get("md_s3_key")
        md_hash   = event.get("md_hash", "")
        if not (doc_id and md_s3_key):
            logger.warning("skip — missing doc_id/md_s3_key in event: %r", event)
            return False
        if RULES_SOURCES and source not in RULES_SOURCES:
            logger.debug("skip — source %s not in RULES_SOURCES=%s", source, RULES_SOURCES)
            return False

        if self._already_extracted(doc_id, md_hash):
            logger.debug("skip — %s already extracted from this md_hash", doc_id)
            return False

        logger.info("extracting rules for %s (md_s3_key=%s)", doc_id, md_s3_key)
        try:
            md_bytes = self._download_md(md_s3_key)
            with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as fh:
                fh.write(md_bytes)
                tmp_path = fh.name
            try:
                result = asyncio.run(self._generate(tmp_path))
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as exc:
            self._record_error(doc_id, str(exc))
            logger.error("rules extraction failed for %s: %s", doc_id, exc)
            return False

        if result.get("status") == "error":
            self._record_error(doc_id, result.get("error") or "unknown")
            logger.error("generator returned error for %s: %s", doc_id, result.get("error"))
            return False

        rules = result.get("rules", [])
        strategy = result.get("strategy", "direct")
        metadata = result.get("metadata", {}) or {}

        self._save_rules(doc_id, rules)
        self._record_success(
            doc_id=doc_id,
            rules_count=len(rules),
            strategy=strategy,
            llm_calls=metadata.get("llm_calls"),
            processing_time_s=metadata.get("processing_time"),
        )
        self._publish(RulesEvent(
            event_type   = "document.rules.ready",
            doc_id       = doc_id,
            source       = source,
            rules_count  = len(rules),
            strategy     = strategy,
            extracted_at = _now_iso(),
        ))
        logger.info("OK %s — %d rules, strategy=%s", doc_id, len(rules), strategy)
        return True

    def _generator_lazy(self) -> RuleGenerator:
        # Per-thread RuleGenerator. AsyncOpenAI binds to whichever event loop
        # first uses it, so we keep one client per thread to avoid the
        # cross-loop httpx errors you'd otherwise get from asyncio.run() in
        # a threadpool.
        gen = getattr(self._tls, "generator", None)
        if gen is None:
            llm = LLMClient.from_env()
            gen = RuleGenerator(
                llm_client=llm,
                max_tokens=int(os.getenv("RULES_MAX_TOKENS",   "50000")),
                chunk_size=int(os.getenv("RULES_CHUNK_SIZE",   "40000")),
                overlap=int(os.getenv("RULES_OVERLAP",         "2000")),
                temperature=float(os.getenv("RULES_TEMPERATURE", "0.0")),
                response_max_tokens=int(os.getenv("RULES_RESPONSE_MAX", "8000")),
            )
            self._tls.generator = gen
        return gen

    def _db(self):
        """Per-thread psycopg connection. Each worker thread gets its own
        connection so cursors don't get crossed."""
        if not hasattr(self._tls, "conn") or self._tls.conn.closed:
            self._tls.conn = psycopg2.connect(
                host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
                user=config.DB_USER, password=config.DB_PASSWORD,
            )
            self._tls.conn.autocommit = True
        return self._tls.conn

    async def _generate(self, md_path: str) -> dict:
        gen = self._generator_lazy()
        return await gen.generate_rules(doc_path=md_path)

    def _already_extracted(self, doc_id: str, md_hash: str) -> bool:
        """Skip if rules already extracted for this MD version."""
        with self._db().cursor() as cur:
            cur.execute(
                """
                SELECT r.status, m.md_hash
                FROM public.rule_extractions r
                JOIN public.documents_md  m USING (doc_id)
                WHERE r.doc_id = %s
                """,
                (doc_id,),
            )
            row = cur.fetchone()
        if not row:
            return False
        status, current_md_hash = row
        return status == "done" and current_md_hash == md_hash

    def _download_md(self, s3_key: str) -> bytes:
        obj = self.s3.get_object(Bucket=MD_S3_BUCKET, Key=s3_key)
        return obj["Body"].read()

    def _save_rules(self, doc_id: str, rules: list) -> None:
        if not rules:
            return
        rows = []
        for r in rules:
            rows.append((
                r["rule_id"],
                doc_id,
                r.get("tag"),
                r.get("title"),
                r["requirement"],
                r.get("verification_method"),
                json.dumps(r.get("positive_examples", []), ensure_ascii=False),
                json.dumps(r.get("negative_examples", []), ensure_ascii=False),
                r.get("severity", "medium"),
                json.dumps(r.get("source") or {}, ensure_ascii=False),
            ))
        with self._db().cursor() as cur:
            # Replace the doc's rules entirely — extraction is deterministic per (doc, md_hash).
            cur.execute("DELETE FROM public.rules WHERE doc_id = %s", (doc_id,))
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO public.rules
                    (rule_id, doc_id, tag, title, requirement, verification_method,
                     positive_examples, negative_examples, severity, source)
                VALUES %s
                """,
                rows,
                template=("(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb)"),
            )

    def _record_success(self, doc_id: str, rules_count: int, strategy: str,
                        llm_calls, processing_time_s) -> None:
        with self._db().cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.rule_extractions
                    (doc_id, status, rules_count, strategy, llm_calls,
                     processing_time_s, extractor_version, extracted_at, error_message)
                VALUES (%s, 'done', %s, %s, %s, %s, %s, NOW(), NULL)
                ON CONFLICT (doc_id) DO UPDATE
                  SET status            = 'done',
                      rules_count       = EXCLUDED.rules_count,
                      strategy          = EXCLUDED.strategy,
                      llm_calls         = EXCLUDED.llm_calls,
                      processing_time_s = EXCLUDED.processing_time_s,
                      extractor_version = EXCLUDED.extractor_version,
                      extracted_at      = NOW(),
                      error_message     = NULL
                """,
                (doc_id, rules_count, strategy, llm_calls, processing_time_s, EXTRACTOR_VERSION),
            )

    def _record_error(self, doc_id: str, error: str) -> None:
        with self._db().cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.rule_extractions
                    (doc_id, status, rules_count, extractor_version, error_message)
                VALUES (%s, 'error', 0, %s, %s)
                ON CONFLICT (doc_id) DO UPDATE
                  SET status        = 'error',
                      error_message = EXCLUDED.error_message,
                      extracted_at  = NOW(),
                      extractor_version = EXCLUDED.extractor_version
                """,
                (doc_id, EXTRACTOR_VERSION, error[:2000]),
            )

    def _publish(self, event: RulesEvent) -> None:
        future = self.producer.send(RULES_TOPIC, value=asdict(event))
        try:
            future.get(timeout=10)
        except KafkaError as exc:
            logger.error("kafka publish failed for %s: %s", event.doc_id, exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="Rules extractor worker")
    ap.add_argument("--once", action="store_true",
                    help="Drain currently-buffered events and exit")
    args = ap.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("kafka").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    RulesExtractor().run(once=args.once)


if __name__ == "__main__":
    main()
