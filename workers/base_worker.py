from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import boto3
import psycopg2
import psycopg2.extras
from botocore.config import Config as BotoConfig
from kafka import KafkaProducer
from kafka.errors import KafkaError
from tenacity import retry, stop_after_attempt, wait_exponential

import config

logger = logging.getLogger(__name__)


@dataclass
class DocMeta:
    doc_id:     str
    name:       str
    source_url: str
    source_id:  str
    category:   str
    language:   str  = "ru"
    extra:      dict = field(default_factory=dict)


@dataclass
class DocEvent:
    event_type:     str   # document.new | document.updated
    source:         str
    doc_id:         str
    doc_name:       str
    category:       str
    language:       str
    source_url:     str
    s3_key:         str
    file_hash:      str
    file_size:      int
    discovered_at:  str


class BaseWorker(ABC):
    """
    Abstract base for all regulatory document workers.

    Subclasses must define:
      source_id   : str  — matches public.sources.source_id
      kafka_topic : str  — Kafka topic to publish events
      s3_prefix   : str  — folder inside S3_PREFIX (e.g. "cbu")

    And implement:
      discover()  → List[DocMeta]
      download()  → bytes
    """

    source_id:   str
    kafka_topic: str
    s3_prefix:   str

    PW_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )

    def __init__(self) -> None:
        self.s3 = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            config=BotoConfig(retries={"max_attempts": 3}, signature_version="s3v4"),
        )
        self._ensure_bucket()
        self.producer = KafkaProducer(
            bootstrap_servers=config.KAFKA_BOOTSTRAP_SERVERS.split(","),
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
            acks="all",
            retries=3,
        )
        self.conn = psycopg2.connect(
            host=config.DB_HOST,
            port=config.DB_PORT,
            dbname=config.DB_NAME,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
        )
        self.conn.autocommit = True

        # Shared Playwright session — initialized lazily, torn down at end of run_once.
        self._pw = None
        self._pw_browser = None
        self._pw_context = None
        self._pw_warmed: set[str] = set()

    def _playwright_context(self):
        """Return a shared Playwright browser context, launching on first use."""
        if self._pw_context is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._pw_browser = self._pw.chromium.launch(headless=True)
            self._pw_context = self._pw_browser.new_context(
                user_agent=self.PW_USER_AGENT,
                locale="en-US",
                viewport={"width": 1280, "height": 720},
            )
        return self._pw_context

    def _playwright_warm_up(self, origin: str) -> None:
        """Visit `origin` once per run so subsequent fetches share its cookies/WAF clearance."""
        if origin in self._pw_warmed:
            return
        try:
            ctx = self._playwright_context()
            page = ctx.new_page()
            page.goto(origin, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(1_000)
            page.close()
            self._pw_warmed.add(origin)
        except Exception as exc:
            logger.warning("[%s] playwright warm-up failed for %s: %s", self.source_id, origin, exc)

    def _playwright_fetch(self, url: str, *, settle_ms: int = 1_500,
                          timeout_ms: int = 60_000) -> bytes:
        """Fetch `url` through the shared Playwright context and return rendered HTML bytes.

        Uses `domcontentloaded` (not `networkidle`) — some WAFs hold the
        connection open forever waiting for analytics beacons, which makes
        `networkidle` never resolve and ignore the per-call timeout.
        We also set hard default timeouts on the context so no operation can
        ever hang indefinitely.
        """
        ctx = self._playwright_context()
        ctx.set_default_timeout(timeout_ms)
        ctx.set_default_navigation_timeout(timeout_ms)
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if settle_ms:
                page.wait_for_timeout(settle_ms)
            return page.content().encode("utf-8")
        finally:
            page.close()

    def _close_playwright(self) -> None:
        for attr in ("_pw_context", "_pw_browser", "_pw"):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                if attr == "_pw":
                    obj.stop()
                else:
                    obj.close()
            except Exception:
                pass
            setattr(self, attr, None)
        self._pw_warmed.clear()

    def _ensure_bucket(self) -> None:
        try:
            self.s3.head_bucket(Bucket=config.S3_BUCKET)
        except Exception:
            try:
                self.s3.create_bucket(Bucket=config.S3_BUCKET)
                logger.info("[%s] created bucket %s", self.source_id, config.S3_BUCKET)
            except Exception as exc:
                logger.warning("[%s] could not ensure bucket %s: %s",
                               self.source_id, config.S3_BUCKET, exc)

    # ── public API ──────────────────────────────────────────────────────────

    def run_once(self) -> int:
        """Discover and process all new/changed documents. Returns count of new/updated."""
        logger.info("[%s] run started", self.source_id)
        self._touch_source()
        docs = self.discover()
        logger.info("[%s] discovered %d documents", self.source_id, len(docs))
        count = 0
        try:
            for doc in docs:
                try:
                    if self._process(doc):
                        count += 1
                except Exception as exc:
                    logger.error("[%s] error processing %s: %s", self.source_id, doc.doc_id, exc, exc_info=True)
        finally:
            try:
                self.producer.flush(timeout=10)
            except Exception as exc:
                logger.warning("[%s] kafka flush failed: %s", self.source_id, exc)
            self._close_playwright()
        logger.info("[%s] run done — new/updated: %d / %d", self.source_id, count, len(docs))
        return count

    def run_loop(self, interval_hours: float) -> None:
        logger.info("[%s] continuous loop started (interval=%.1fh)", self.source_id, interval_hours)
        while True:
            try:
                self.run_once()
            except Exception as exc:
                logger.error("[%s] run_once failed: %s", self.source_id, exc, exc_info=True)
            time.sleep(interval_hours * 3600)

    # ── internals ───────────────────────────────────────────────────────────

    MIN_CONTENT_BYTES = 1_024  # smaller payloads are almost always WAF stubs / error pages

    def _process(self, doc: DocMeta) -> bool:
        content  = self.download(doc)
        if len(content) < self.MIN_CONTENT_BYTES:
            logger.warning("[%s] skipping %s — suspiciously small payload (%d bytes)",
                           self.source_id, doc.doc_id, len(content))
            return False

        hash_id  = hashlib.sha256(content).hexdigest()   # unique content fingerprint

        # Skip if this exact content version already exists anywhere in DB
        if self._hash_exists(hash_id):
            logger.debug("[%s] content unchanged (hash_id=%s…): %s", self.source_id, hash_id[:8], doc.doc_id)
            return False

        is_new  = not self._doc_exists(doc.doc_id)
        ext     = _guess_ext(content)
        s3_key  = f"{config.S3_PREFIX}/{self.s3_prefix}/{doc.doc_id}/{doc.doc_id}{ext}"

        self._upload_s3(content, s3_key)
        self._upsert_db(doc, s3_key, hash_id, len(content), is_new)
        self._save_extra(doc)
        self._publish(DocEvent(
            event_type    = "document.new" if is_new else "document.updated",
            source        = self.source_id,
            doc_id        = doc.doc_id,
            doc_name      = doc.name,
            category      = doc.category,
            language      = doc.language,
            source_url    = doc.source_url,
            s3_key        = s3_key,
            file_hash     = hash_id,
            file_size     = len(content),
            discovered_at = _now_iso(),
        ))
        logger.info("[%s] %s — %s (hash_id=%s…)", self.source_id,
                    "NEW" if is_new else "UPDATED", doc.name[:80], hash_id[:8])
        return True

    def _hash_exists(self, hash_id: str) -> bool:
        """Return True if this exact content (by SHA-256) already exists in DB."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM public.documents WHERE hash_id = %s", (hash_id,))
            return cur.fetchone() is not None

    def _doc_exists(self, doc_id: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM public.documents WHERE doc_id = %s", (doc_id,))
            return cur.fetchone() is not None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _upload_s3(self, content: bytes, s3_key: str) -> None:
        self.s3.put_object(Bucket=config.S3_BUCKET, Key=s3_key, Body=content)

    def _upsert_db(
        self,
        doc: DocMeta,
        s3_key: str,
        hash_id: str,
        file_size: int,
        is_new: bool,
    ) -> None:
        with self.conn.cursor() as cur:
            if is_new:
                cur.execute(
                    """
                    INSERT INTO public.documents
                        (doc_id, hash_id, source_id, name, source_url,
                         s3_key, file_size, category, language)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (doc_id) DO NOTHING
                    """,
                    (doc.doc_id, hash_id, doc.source_id, doc.name, doc.source_url,
                     s3_key, file_size, doc.category, doc.language),
                )
            else:
                # If another doc_id already owns this hash (rare), don't crash on the unique constraint.
                cur.execute(
                    """
                    UPDATE public.documents
                    SET s3_key=%s, hash_id=%s, file_size=%s,
                        last_updated_at=NOW(), processing_status='pending'
                    WHERE doc_id=%s
                      AND NOT EXISTS (
                        SELECT 1 FROM public.documents d2
                        WHERE d2.hash_id=%s AND d2.doc_id<>%s
                      )
                    """,
                    (s3_key, hash_id, file_size, doc.doc_id, hash_id, doc.doc_id),
                )

    def _touch_source(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE public.sources SET last_checked_at=NOW() WHERE source_id=%s",
                (self.source_id,),
            )

    def _publish(self, event: DocEvent) -> None:
        future = self.producer.send(self.kafka_topic, value=asdict(event))
        try:
            future.get(timeout=10)
        except KafkaError as exc:
            logger.error("[%s] kafka publish failed: %s", self.source_id, exc)

    # ── optional hook for site-specific DB tables ────────────────────────────
    def _save_extra(self, doc: DocMeta) -> None:
        """Override in subclasses to write to site-specific schema tables."""
        pass

    # ── subclass interface ───────────────────────────────────────────────────

    @abstractmethod
    def discover(self) -> List[DocMeta]:
        """Return list of all documents found on the source site."""

    @abstractmethod
    def download(self, doc: DocMeta) -> bytes:
        """Download and return raw file content for a given document."""


# ── helpers ─────────────────────────────────────────────────────────────────

def _guess_ext(content: bytes) -> str:
    head = content[:1024].lstrip().lower()
    if content[:4] == b"%PDF":
        return ".pdf"
    if content[:2] == b"PK":
        return ".docx"
    # Detect XHTML/HTML before generic XML — many EUR-Lex pages declare <?xml
    # but the root element is <html>, which is conceptually still HTML.
    if b"<html" in head[:512] or head.startswith(b"<!doctype"):
        return ".html"
    if head.startswith(b"<?xml"):
        return ".xml"
    return ".bin"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
