"""
EUR-Lex Worker — European Union Regulations
https://eur-lex.europa.eu/

Discovers fintech-relevant EU regulations via the search API,
then downloads the HTML content using Playwright (bypasses AWS WAF
that blocks direct httpx requests from datacenter IPs).
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
    ("payment services directive",          "payments"),
    ("electronic money institution",        "payments"),
    ("SEPA credit transfer",                "payments"),
    ("anti-money laundering directive",     "aml_cft"),
    ("transfer of funds regulation",        "aml_cft"),
    ("crypto-assets regulation",            "crypto"),
    ("markets in crypto",                   "crypto"),
    ("digital operational resilience",      "cybersecurity"),
    ("network information security",        "cybersecurity"),
    ("general data protection regulation",  "data_protection"),
    ("artificial intelligence act",         "ai_scoring"),
    ("consumer credit directive",           "consumer_protection"),
    ("digital identity eIDAS",              "kyc"),
    ("banking regulation capital",          "licensing"),
    ("open banking financial data",         "payments"),
]


def _pdf_url(celex: str) -> str:
    return f"{BASE_URL}/legal-content/EN/TXT/PDF/?uri=CELEX:{celex}"

def _html_url(celex: str) -> str:
    return f"{BASE_URL}/legal-content/EN/TXT/HTML/?uri=CELEX:{celex}"

def _search_url(query: str, page: int = 1) -> str:
    q = query.replace(" ", "+")
    return (f"{BASE_URL}/search.html?text={q}&lang=en&type=quick&scope=EURLEX"
            f"&DB_TYPE_OF_ACT=directive,regulation&page={page}")


class EurLexWorker(BaseWorker):
    source_id   = "eurlex"
    kafka_topic = config.TOPIC_EURLEX
    s3_prefix   = "eurlex"

    def discover(self) -> List[DocMeta]:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        seen_celex: set[str] = set()
        docs: List[DocMeta] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=config.HTTP_HEADERS.get("User-Agent", "Mozilla/5.0"),
                    locale="en-US",
                )
                page = context.new_page()

                for query, category in FINTECH_QUERIES:
                    logger.info("[eurlex] searching: %r", query)
                    for pg in range(1, 4):  # up to 3 pages per query = ~30 results
                        url = _search_url(query, pg)
                        page.goto(url, wait_until="networkidle", timeout=30_000)
                        soup = BeautifulSoup(page.content(), "lxml")

                        # Extract unique CELEX numbers from search results
                        # Skip consolidated versions (CELEX starting with '0')
                        celex_links = soup.find_all(
                            "a",
                            href=re.compile(r"uri=CELEX:[3-9]\w+"),
                        )

                        found = 0
                        for link in celex_links:
                            m = re.search(r"CELEX:([3-9]\w+)", link["href"])
                            if not m:
                                continue
                            celex = m.group(1)
                            if celex in seen_celex:
                                continue
                            seen_celex.add(celex)

                            # Extract title from the AUTO link (full title)
                            title = link.get_text(strip=True)
                            if not title or len(title) < 10:
                                continue

                            doc_id = f"eurlex-{celex}"
                            docs.append(DocMeta(
                                doc_id     = doc_id,
                                name       = title,
                                source_url = _html_url(celex),
                                source_id  = self.source_id,
                                category   = category,
                                language   = "en",
                                extra      = {"celex": celex, "query": query},
                            ))
                            found += 1

                        logger.debug("[eurlex] query=%r page=%d: %d new CELEX", query, pg, found)

                        # Stop paginating if no new results on this page
                        if found == 0:
                            break

                        # Check if there's a next page link
                        next_link = soup.find("a", href=re.compile(rf"page={pg + 1}"))
                        if not next_link:
                            break

            finally:
                browser.close()

        logger.info("[eurlex] discovered %d unique regulations", len(docs))
        return docs

    def download(self, doc: DocMeta) -> bytes:
        """Download document content.

        Strategy (in order):
        1. CELLAR API (publications.europa.eu) — EU's programmatic access endpoint,
           different WAF from eur-lex.europa.eu, designed for machine access.
        2. EUR-Lex XML format via httpx — less WAF-targeted than HTML.
        3. EUR-Lex HTML via Playwright — last resort for WAF bypass.
        """
        celex = doc.extra.get("celex", "")

        content = self._fetch_cellar(celex)
        if len(content) > 10_000:
            logger.info("[eurlex] CELLAR OK %s (%d bytes)", celex, len(content))
            return content

        content = self._fetch_xml(celex)
        if len(content) > 10_000:
            logger.info("[eurlex] XML OK %s (%d bytes)", celex, len(content))
            return content

        content = self._pw_fetch_html(_html_url(celex))
        if len(content) > 30_000:
            logger.info("[eurlex] Playwright HTML OK %s (%d bytes)", celex, len(content))
            return content

        logger.error("[eurlex] all sources failed for %s — storing stub", celex)
        return f"EUR-Lex {celex} — {doc.name}\nBlocked or unavailable.\n".encode()

    def _fetch_cellar(self, celex: str) -> bytes:
        """Fetch via CELLAR API (publications.europa.eu) — EU's official machine-access endpoint."""
        import httpx
        url = f"https://publications.europa.eu/resource/celex/{celex}"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
        }
        try:
            with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client:
                r = client.get(url)
                if r.status_code == 200:
                    return r.content
        except Exception as exc:
            logger.debug("[eurlex] CELLAR failed for %s: %s", celex, exc)
        return b""

    def _fetch_xml(self, celex: str) -> bytes:
        """Fetch EUR-Lex XML format via httpx (less WAF-targeted than HTML)."""
        import httpx
        url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/XML/?uri=CELEX:{celex}"
        headers = {
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
        }
        try:
            with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as client:
                r = client.get(url)
                if r.status_code == 200 and len(r.content) > 10_000:
                    return r.content
        except Exception as exc:
            logger.debug("[eurlex] XML fetch failed for %s: %s", celex, exc)
        return b""

    def _save_extra(self, doc: DocMeta) -> None:
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
                    doc.extra.get("celex"),
                    doc.extra.get("celex"),
                    doc.name,
                    None,
                    date.today(),
                    _pdf_url(doc.extra.get("celex", "")),
                ),
            )

    def _pw_fetch_html(self, url: str) -> bytes:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/121.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    viewport={"width": 1280, "height": 720},
                )
                page = context.new_page()
                # Warm up: visit homepage first so WAF sees a session with cookies + referrer
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=20_000)
                page.wait_for_timeout(1_000)
                page.goto(url, wait_until="networkidle", timeout=60_000)
                return page.content().encode("utf-8")
            finally:
                browser.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    EurLexWorker().run_loop(interval_hours=config.EURLEX_INTERVAL_HOURS)
