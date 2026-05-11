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

        # LLM client + generator initialised lazily on first message —
        # Yandex creds aren't strictly required to *start* the consumer.
        self._generator: RuleGenerator | None = None

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
            max_poll_records=1,
            max_poll_interval_ms=int(os.getenv("RULES_MAX_POLL_INTERVAL_MS", "1800000")),  # 30 min
            session_timeout_ms=int(os.getenv("RULES_SESSION_TIMEOUT_MS",   "60000")),
            heartbeat_interval_ms=int(os.getenv("RULES_HEARTBEAT_MS",      "10000")),
            request_timeout_ms=int(os.getenv("RULES_REQUEST_TIMEOUT_MS",   "65000")),
        )
        logger.info("subscribed to %s (group=%s) → bucket=%s out=%s",
                    MD_TOPIC, RULES_CONSUMER_GROUP, MD_S3_BUCKET, RULES_TOPIC)

        processed = 0
        try:
            for msg in consumer:
                try:
                    if self._handle(msg.value):
                        processed += 1
                except Exception as exc:
                    logger.error("error handling %s/%d/%d: %s",
                                 msg.topic, msg.partition, msg.offset, exc, exc_info=True)
                try:
                    consumer.commit()
                except CommitFailedError as exc:
                    logger.warning("commit failed (rebalance?), retry on next poll: %s", exc)
        except StopIteration:
            pass
        finally:
            try:
                self.producer.flush(timeout=10)
            except Exception:
                pass
            consumer.close()
        logger.info("done. processed=%d", processed)
        return processed

    # ── internals ───────────────────────────────────────────────────────────

    def _handle(self, event: dict) -> bool:
        doc_id    = event.get("doc_id")
        source    = event.get("source")
        md_s3_key = event.get("md_s3_key")
        md_hash   = event.get("md_hash", "")
        if not (doc_id and md_s3_key):
            logger.warning("skip — missing doc_id/md_s3_key in event: %r", event)
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
        if self._generator is None:
            llm = LLMClient.from_env()
            self._generator = RuleGenerator(
                llm_client=llm,
                max_tokens=int(os.getenv("RULES_MAX_TOKENS",   "50000")),
                chunk_size=int(os.getenv("RULES_CHUNK_SIZE",   "40000")),
                overlap=int(os.getenv("RULES_OVERLAP",         "2000")),
                temperature=float(os.getenv("RULES_TEMPERATURE", "0.0")),
                response_max_tokens=int(os.getenv("RULES_RESPONSE_MAX", "8000")),
            )
        return self._generator

    async def _generate(self, md_path: str) -> dict:
        gen = self._generator_lazy()
        return await gen.generate_rules(doc_path=md_path)

    def _already_extracted(self, doc_id: str, md_hash: str) -> bool:
        """Skip if rules already extracted for this MD version."""
        with self.conn.cursor() as cur:
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
        with self.conn.cursor() as cur:
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
        with self.conn.cursor() as cur:
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
        with self.conn.cursor() as cur:
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
