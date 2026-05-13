"""
MD Converter Worker — consumes downloaded-document events from all source topics
(reg.cbu, reg.lex, reg.eurlex), pulls the raw file from MinIO, converts it to
Markdown via docling, uploads the .md to a second MinIO bucket (regtech-md),
records the result in public.documents_md, and publishes a reg.md event for
downstream chunking/embedding.

Run:
    python md_converter.py             # consume forever (production)
    python md_converter.py --once      # drain currently-buffered events and exit (smoke test)

Configurable via env (see config.py):
    MD_S3_BUCKET            default: regtech-md
    MD_TOPIC                default: reg.md
    MD_CONSUMER_GROUP       default: md-converter
    MD_POLL_TIMEOUT_MS      default: 5000
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

import boto3
import psycopg2
from botocore.config import Config as BotoConfig
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import CommitFailedError, KafkaError

sys.path.insert(0, os.path.dirname(__file__))
import config

logger = logging.getLogger("md_converter")

CONVERTER_VERSION = "docling-1"

MD_S3_BUCKET      = os.getenv("MD_S3_BUCKET",       "regtech-md")
MD_TOPIC          = os.getenv("MD_TOPIC",           "reg.md")
MD_CONSUMER_GROUP = os.getenv("MD_CONSUMER_GROUP",  "md-converter")
POLL_TIMEOUT_MS   = int(os.getenv("MD_POLL_TIMEOUT_MS", "5000"))

INPUT_TOPICS = [config.TOPIC_CBU, config.TOPIC_LEX, config.TOPIC_EURLEX]


@dataclass
class MdEvent:
    event_type:   str
    doc_id:       str
    source:       str
    md_s3_key:    str
    md_size:      int
    md_hash:      str
    converted_at: str


class MdConverter:
    def __init__(self) -> None:
        self.s3 = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            config=BotoConfig(retries={"max_attempts": 3}, signature_version="s3v4"),
        )
        self._ensure_bucket(MD_S3_BUCKET)

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

        # Lazy: docling is heavy, only import when we have at least one event.
        self._docling = None

    # ── public API ──────────────────────────────────────────────────────────

    def run(self, once: bool = False) -> int:
        """Consume from all source topics. With once=True, drain currently-buffered
        events and exit; otherwise loop forever."""
        consumer = KafkaConsumer(
            *INPUT_TOPICS,
            bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS.split(","),
            group_id=MD_CONSUMER_GROUP,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            consumer_timeout_ms=POLL_TIMEOUT_MS if once else float("inf"),
            # Give slow conversions plenty of headroom before the broker
            # decides we're dead and kicks us out of the group.
            max_poll_records=1,
            max_poll_interval_ms=int(os.getenv("MD_MAX_POLL_INTERVAL_MS", "1800000")),  # 30 min
            session_timeout_ms=int(os.getenv("MD_SESSION_TIMEOUT_MS",   "60000")),     # 60 s
            heartbeat_interval_ms=int(os.getenv("MD_HEARTBEAT_MS",      "10000")),     # 10 s
            request_timeout_ms=int(os.getenv("MD_REQUEST_TIMEOUT_MS",   "65000")),     # > session
        )
        logger.info("subscribed to %s (group=%s) → bucket=%s topic=%s",
                    INPUT_TOPICS, MD_CONSUMER_GROUP, MD_S3_BUCKET, MD_TOPIC)

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
                    # Consumer was kicked from the group (slow rebalance). The next
                    # poll re-joins us; just log and keep going — at-least-once is fine
                    # because _already_converted() skips duplicates.
                    logger.warning("commit failed (rebalance?), will retry on next poll: %s", exc)
        except StopIteration:
            pass  # consumer_timeout_ms reached in --once mode
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
        doc_id   = event.get("doc_id")
        source   = event.get("source")
        s3_key   = event.get("s3_key")
        doc_name = event.get("doc_name", "")
        if not (doc_id and s3_key):
            logger.warning("skip — missing doc_id/s3_key in event: %r", event)
            return False

        if self._already_converted(doc_id, event.get("file_hash", "")):
            logger.debug("skip — %s already converted with current hash", doc_id)
            return False

        logger.info("converting %s — %s", doc_id, doc_name[:60])
        try:
            raw       = self._download_raw(s3_key)
            md_text   = self._convert(raw, s3_key)
        except Exception as exc:
            self._record_error(doc_id, str(exc))
            logger.error("conversion failed for %s: %s", doc_id, exc)
            return False

        md_bytes  = md_text.encode("utf-8")
        md_hash   = hashlib.sha256(md_bytes).hexdigest()
        md_key    = f"{source}/{doc_id}/{doc_id}.md"

        self._upload_md(md_bytes, md_key)
        self._record_success(doc_id, md_key, len(md_bytes), md_hash)
        self._publish(MdEvent(
            event_type   = "document.md.ready",
            doc_id       = doc_id,
            source       = source,
            md_s3_key    = md_key,
            md_size      = len(md_bytes),
            md_hash      = md_hash,
            converted_at = _now_iso(),
        ))
        logger.info("OK %s → %s (%d bytes md)", doc_id, md_key, len(md_bytes))
        return True

    def _already_converted(self, doc_id: str, raw_hash: str) -> bool:
        """Skip if we already have a successful conversion of this exact content version.

        We compare against the raw doc's hash recorded in public.documents to know
        whether the source changed. If md row exists AND raw hash matches, skip.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.status, d.hash_id
                FROM public.documents_md m
                JOIN public.documents    d USING (doc_id)
                WHERE m.doc_id = %s
                """,
                (doc_id,),
            )
            row = cur.fetchone()
        if not row:
            return False
        status, current_hash = row
        return status == "done" and current_hash == raw_hash

    def _download_raw(self, s3_key: str) -> bytes:
        obj = self.s3.get_object(Bucket=config.S3_BUCKET, Key=s3_key)
        return obj["Body"].read()

    def _convert(self, raw: bytes, s3_key: str) -> str:
        """Convert raw bytes to markdown using docling. The library detects the
        format from the file extension, so write to a temp file with the right
        suffix before invoking."""
        if self._docling is None:
            # docling pulls in transformers — defer until first event.
            from docling.document_converter import DocumentConverter
            self._docling = DocumentConverter()

        suffix = os.path.splitext(s3_key)[1] or ".html"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(raw)
            tmp_path = fh.name
        try:
            result = self._docling.convert(tmp_path)
            return result.document.export_to_markdown()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _upload_md(self, content: bytes, md_key: str) -> None:
        self.s3.put_object(Bucket=MD_S3_BUCKET, Key=md_key, Body=content,
                           ContentType="text/markdown; charset=utf-8")

    def _record_success(self, doc_id: str, md_key: str, size: int, md_hash: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.documents_md
                    (doc_id, md_s3_key, md_size, md_hash, converted_at,
                     converter_version, status, error_message)
                VALUES (%s, %s, %s, %s, NOW(), %s, 'done', NULL)
                ON CONFLICT (doc_id) DO UPDATE
                  SET md_s3_key       = EXCLUDED.md_s3_key,
                      md_size         = EXCLUDED.md_size,
                      md_hash         = EXCLUDED.md_hash,
                      converted_at    = NOW(),
                      converter_version = EXCLUDED.converter_version,
                      status          = 'done',
                      error_message   = NULL
                """,
                (doc_id, md_key, size, md_hash, CONVERTER_VERSION),
            )

    def _record_error(self, doc_id: str, error: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.documents_md (doc_id, converter_version, status, error_message)
                VALUES (%s, %s, 'error', %s)
                ON CONFLICT (doc_id) DO UPDATE
                  SET status        = 'error',
                      error_message = EXCLUDED.error_message,
                      converted_at  = NOW(),
                      converter_version = EXCLUDED.converter_version
                """,
                (doc_id, CONVERTER_VERSION, error[:2000]),
            )

    def _publish(self, event: MdEvent) -> None:
        future = self.producer.send(MD_TOPIC, value=asdict(event))
        try:
            future.get(timeout=10)
        except KafkaError as exc:
            logger.error("kafka publish failed for %s: %s", event.doc_id, exc)

    def _ensure_bucket(self, bucket: str) -> None:
        try:
            self.s3.head_bucket(Bucket=bucket)
        except Exception:
            try:
                self.s3.create_bucket(Bucket=bucket)
                logger.info("created bucket %s", bucket)
            except Exception as exc:
                logger.warning("could not ensure bucket %s: %s", bucket, exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="MD converter worker")
    ap.add_argument("--once", action="store_true",
                    help="Drain currently-buffered events and exit (smoke test)")
    args = ap.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Silence kafka-python's chatty info logs
    logging.getLogger("kafka").setLevel(logging.WARNING)

    MdConverter().run(once=args.once)


if __name__ == "__main__":
    main()
