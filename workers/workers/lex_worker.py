"""
Lex Worker — Lex.uz National Legislation of Uzbekistan
https://lex.uz/ru/

Entry point: /ru/search/ext?lang=1&okoz=6536 — the "Законодательство о
финансах и кредите. Банковская деятельность" classifier rollup (~7447 docs
across ~373 pages of 20 results each, newest first). Every result is by
definition finance/banking law, so we don't post-filter by title.

Pagination is ASP.NET __doPostBack: the visible window is 1..10 with a
"Следующий" button that advances by one page (so we click it to cross the
window boundary). Walk until the next button is gone.

Both discover() and download() share the BaseWorker Playwright session
(one Chromium per run, not one per document).

Publishes to Kafka topic: reg.lex
"""

from __future__ import annotations

import logging
import re
import sys
import os
from typing import List, Optional
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base_worker import BaseWorker, DocMeta
import config

logger = logging.getLogger(__name__)

BASE_URL   = "https://lex.uz"
SEARCH_URL = f"{BASE_URL}/ru/search/ext?lang=1&okoz=6536"

# Effectively uncapped — discover() stops when there is no "next" button.
MAX_PAGES = int(os.getenv("LEX_MAX_PAGES", "1000"))

# Title-based category assignment (NOT a filter — we keep every doc).
# Falls back to None (NULL category) when no pattern matches.
TITLE_CATEGORIES: list[tuple[str, str]] = [
    (r"(отмыван|финансир.{0,10}террор|легализац|пфм|aml|финмонитор|подозрительн)", "aml_cft"),
    (r"(платежн|перевод|электронн.?деньг|кошел[её]к|эквайринг|p2p|расчет|sepa)", "payments"),
    (r"(идентификац|верификац|kyc|надлежащ.?проверк|onboarding)", "kyc"),
    (r"(персональн.?данн|приватност|защит.?персон)", "data_protection"),
    (r"(лицензи|допуск|разрешени.{0,15}(банк|платеж|финанс|кредитн))", "licensing"),
    (r"(информационн.?безопасност|киберб|защит.?(информ|систем))", "cybersecurity"),
    (r"(потребител.{0,20}(защит|прав|кредит)|защит.?потребител)", "consumer_protection"),
    (r"(крипт|цифров.?актив|блокчейн|токен|виртуальн.?валют)", "crypto"),
    (r"(искусственн.?интеллект|скоринг|алгоритм.?(решени|кредит))", "ai_scoring"),
    (r"(отчетност|аудит|надзор.{0,15}(банк|финанс))", "reporting"),
]


def _classify_title(title: str) -> Optional[str]:
    t = title.lower()
    for pattern, category in TITLE_CATEGORIES:
        if re.search(pattern, t):
            return category
    return None  # let the AI core classify later


class LexWorker(BaseWorker):
    source_id   = "lex"
    kafka_topic = config.TOPIC_LEX
    s3_prefix   = "lex"

    def discover(self) -> List[DocMeta]:
        from bs4 import BeautifulSoup

        docs: List[DocMeta] = []
        seen: set[str] = set()

        ctx = self._playwright_context()
        page = ctx.new_page()
        try:
            logger.info("[lex] loading search page: %s", SEARCH_URL)
            page.goto(SEARCH_URL, wait_until="networkidle", timeout=30_000)
            page.wait_for_timeout(2_000)

            for page_num in range(1, MAX_PAGES + 1):
                html  = self._safe_page_content(page)
                soup  = BeautifulSoup(html, "lxml")
                links = soup.find_all("a", href=re.compile(r"^/ru/docs/\-?\d+$"))

                kept = 0
                for link in links:
                    href    = link["href"]
                    lex_id  = href.strip("/").split("/")[-1].lstrip("-")
                    doc_id  = f"lex-{lex_id}"
                    if doc_id in seen:
                        continue
                    seen.add(doc_id)

                    title    = link.get_text(strip=True) or doc_id
                    category = _classify_title(title)
                    docs.append(DocMeta(
                        doc_id     = doc_id,
                        name       = title,
                        source_url = urljoin(BASE_URL, href),
                        source_id  = self.source_id,
                        category   = category,
                    ))
                    kept += 1

                if page_num % 10 == 1 or page_num <= 5:
                    logger.info("[lex] page %d: %d links, %d new (running total: %d)",
                                page_num, len(links), kept, len(docs))

                if page_num >= MAX_PAGES:
                    logger.info("[lex] reached LEX_MAX_PAGES=%d; stopping", MAX_PAGES)
                    break
                if not self._goto_next_page(page, page_num):
                    logger.info("[lex] no next page after page %d — done", page_num)
                    break
                page.wait_for_timeout(1_500)
        finally:
            page.close()

        logger.info("[lex] discovered %d docs total", len(docs))
        return docs

    @staticmethod
    def _safe_page_content(page, attempts: int = 5) -> str:
        """Read page.content() while the page may still be navigating.

        ASP.NET postbacks can fire a second navigation milliseconds after the
        first one settled, so a single `page.content()` call sometimes throws
        'page is navigating'. Retry with `networkidle` waits in between.
        """
        import time
        last_exc = None
        for i in range(attempts):
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
                page.wait_for_load_state("domcontentloaded")
                return page.content()
            except Exception as exc:
                last_exc = exc
                time.sleep(0.5 * (i + 1))
        raise last_exc if last_exc else RuntimeError("page.content() failed")

    def _goto_next_page(self, page, current_page: int) -> bool:
        """Click the (current_page+1) button via __doPostBack.

        Collects all paginator anchors' text+href in a single page.evaluate()
        so the live ElementHandles don't go stale if the page navigates while
        we're inspecting them (an ASP.NET postback can fire mid-iteration).
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        try:
            candidates = page.evaluate("""
                () => Array.from(
                    document.querySelectorAll('a.btn_pgn_extend, a.btn_pgn')
                ).map(a => ({
                    text: (a.textContent || '').trim(),
                    href: a.getAttribute('href') || ''
                }))
            """) or []
        except Exception:
            return False

        target_text = str(current_page + 1)
        next_labels = {"следующий", "next", "›", ">"}

        def _try_click(entry: dict) -> bool:
            m = re.search(r"__doPostBack\('([^']+)'", entry.get("href", ""))
            if not m:
                return False
            try:
                page.evaluate(f"__doPostBack('{m.group(1)}', '')")
                page.wait_for_load_state("networkidle", timeout=20_000)
                return True
            except (PWTimeout, Exception):
                return False

        for entry in candidates:
            if entry["text"] == target_text and _try_click(entry):
                return True
        for entry in candidates:
            if entry["text"].lower() in next_labels and _try_click(entry):
                return True
        return False

    def download(self, doc: DocMeta) -> bytes:
        """Render the lex.uz doc page and return its HTML."""
        try:
            return self._playwright_fetch(doc.source_url, settle_ms=2_000, timeout_ms=45_000)
        except Exception as exc:
            logger.error("[lex] download %s failed: %s", doc.source_url, exc)
            return b""

    def _save_extra(self, doc: DocMeta) -> None:
        lex_id = doc.doc_id.replace("lex-", "")
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lex.acts (doc_id, lex_id, title_ru, detail_url)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (lex_id) DO UPDATE SET title_ru = EXCLUDED.title_ru
                """,
                (doc.doc_id, lex_id, doc.name, doc.source_url),
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    LexWorker().run_once()
