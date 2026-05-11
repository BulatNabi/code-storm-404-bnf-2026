"""
EUR-Lex Worker — European Union Regulations
https://eur-lex.europa.eu/

Discovers fintech-relevant regulations via the search UI, then downloads
the rendered HTML view of each document. EUR-Lex sits behind an Akamai
WAF that rejects datacenter httpx traffic, so all fetches go through the
shared Playwright session in BaseWorker (warmed up with one homepage hit
so the WAF cookie is seeded before doc requests).

Publishes to Kafka topic: reg.eurlex
"""

from __future__ import annotations

import logging
import re
import sys
import os
from datetime import date
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base_worker import BaseWorker, DocMeta
import config

logger = logging.getLogger(__name__)

BASE_URL = "https://eur-lex.europa.eu"

# Fintech search queries → category mapping
FINTECH_QUERIES: list[tuple[str, str]] = [
    # Payments / e-money / open banking
    ("payment services directive",          "payments"),
    ("electronic money institution",        "payments"),
    ("SEPA credit transfer",                "payments"),
    ("instant credit transfer",             "payments"),
    ("open banking financial data",         "payments"),
    ("PSD3 payment services regulation",    "payments"),
    # AML / sanctions
    ("anti-money laundering directive",     "aml_cft"),
    ("transfer of funds regulation",        "aml_cft"),
    ("AMLA authority",                      "aml_cft"),
    ("sixth AML directive",                 "aml_cft"),
    ("restrictive measures sanctions",      "aml_cft"),
    # Crypto / MiCA
    ("crypto-assets regulation",            "crypto"),
    ("markets in crypto",                   "crypto"),
    ("MiCA crypto",                         "crypto"),
    ("virtual asset service providers",     "crypto"),
    # Cybersecurity / DORA / NIS
    ("digital operational resilience",      "cybersecurity"),
    ("DORA financial entities",             "cybersecurity"),
    ("network information security",        "cybersecurity"),
    ("NIS2 directive",                      "cybersecurity"),
    # Data / privacy / AI
    ("general data protection regulation",  "data_protection"),
    ("data governance act",                 "data_protection"),
    ("artificial intelligence act",         "ai_scoring"),
    ("ai liability directive",              "ai_scoring"),
    # Consumer / identity / banking
    ("consumer credit directive",           "consumer_protection"),
    ("digital identity eIDAS",              "kyc"),
    ("eIDAS 2 wallet",                      "kyc"),
    ("banking regulation capital",          "licensing"),
    ("capital requirements directive",      "licensing"),
    ("investment firms regulation",         "licensing"),
]

# How many search-result pages to walk per query (≈20 CELEX hits/page)
PAGES_PER_QUERY = int(os.getenv("EURLEX_PAGES_PER_QUERY", "5"))


def _html_url(celex: str) -> str:
    return f"{BASE_URL}/legal-content/EN/TXT/HTML/?uri=CELEX:{celex}"

def _pdf_url(celex: str) -> str:
    return f"{BASE_URL}/legal-content/EN/TXT/PDF/?uri=CELEX:{celex}"

def _search_url(query: str, page: int = 1) -> str:
    q = query.replace(" ", "+")
    return (f"{BASE_URL}/search.html?text={q}&lang=en&type=quick&scope=EURLEX"
            f"&DB_TYPE_OF_ACT=directive,regulation&page={page}")


class EurLexWorker(BaseWorker):
    source_id   = "eurlex"
    kafka_topic = config.TOPIC_EURLEX
    s3_prefix   = "eurlex"

    MIN_DOC_BYTES = 30_000  # a real EUR-Lex HTML view is hundreds of KB; smaller = WAF challenge

    def discover(self) -> List[DocMeta]:
        from bs4 import BeautifulSoup

        seen_celex: set[str] = set()
        docs: List[DocMeta] = []

        self._playwright_warm_up(BASE_URL + "/")
        ctx = self._playwright_context()
        page = ctx.new_page()
        try:
            for query, category in FINTECH_QUERIES:
                logger.info("[eurlex] searching: %r", query)
                for pg in range(1, PAGES_PER_QUERY + 1):
                    url = _search_url(query, pg)
                    try:
                        page.goto(url, wait_until="networkidle", timeout=30_000)
                    except Exception as exc:
                        logger.warning("[eurlex] search nav failed (%r p%d): %s", query, pg, exc)
                        break

                    soup = BeautifulSoup(page.content(), "lxml")

                    # CELEX numbers starting with 3-9 are originals; skip consolidated (start with 0)
                    celex_links = soup.find_all("a", href=re.compile(r"uri=CELEX:[3-9]\w+"))

                    found = 0
                    for link in celex_links:
                        m = re.search(r"CELEX:([3-9]\w+)", link["href"])
                        if not m:
                            continue
                        celex = m.group(1)
                        if celex in seen_celex:
                            continue
                        seen_celex.add(celex)

                        title = link.get_text(strip=True)
                        if not title or len(title) < 10:
                            continue

                        docs.append(DocMeta(
                            doc_id     = f"eurlex-{celex}",
                            name       = title,
                            source_url = _html_url(celex),
                            source_id  = self.source_id,
                            category   = category,
                            language   = "en",
                            extra      = {"celex": celex, "query": query},
                        ))
                        found += 1

                    logger.debug("[eurlex] query=%r page=%d: %d new CELEX", query, pg, found)
                    if found == 0:
                        break
                    if not soup.find("a", href=re.compile(rf"page={pg + 1}")):
                        break
        finally:
            page.close()

        logger.info("[eurlex] discovered %d unique regulations", len(docs))
        return docs

    def download(self, doc: DocMeta) -> bytes:
        celex = doc.extra.get("celex", "")
        if not celex:
            return b""

        # The Playwright HTML view is the only path that reliably bypasses the WAF.
        try:
            content = self._playwright_fetch(_html_url(celex), settle_ms=1_500, timeout_ms=60_000)
            if len(content) >= self.MIN_DOC_BYTES:
                return content
            logger.warning("[eurlex] HTML view returned only %d bytes for %s — retrying TXT view",
                           len(content), celex)
        except Exception as exc:
            logger.warning("[eurlex] HTML view failed for %s: %s", celex, exc)

        # Fallback: the canonical /TXT/ view (no /HTML/ segment)
        try:
            txt_url = f"{BASE_URL}/legal-content/EN/TXT/?uri=CELEX:{celex}"
            content = self._playwright_fetch(txt_url, settle_ms=1_500, timeout_ms=60_000)
            if len(content) >= self.MIN_DOC_BYTES:
                return content
            logger.warning("[eurlex] TXT view returned only %d bytes for %s", len(content), celex)
        except Exception as exc:
            logger.warning("[eurlex] TXT view failed for %s: %s", celex, exc)

        return b""

    def _save_extra(self, doc: DocMeta) -> None:
        celex = doc.extra.get("celex")
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO eurlex.regulations
                    (doc_id, celex_number, regulation_code, regulation_name,
                     official_ref, version_date, pdf_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (celex_number) DO UPDATE
                    SET version_date = EXCLUDED.version_date,
                        checked_at   = NOW()
                """,
                (
                    doc.doc_id,
                    celex,
                    celex,
                    doc.name,
                    None,
                    date.today(),
                    _pdf_url(celex or ""),
                ),
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    EurLexWorker().run_once()
