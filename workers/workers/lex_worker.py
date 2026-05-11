"""
Lex Worker — Lex.uz National Legislation of Uzbekistan
https://lex.uz/ru/

Navigates via Playwright because pagination uses ASP.NET __doPostBack (not URL params).
Entry point: /ru/search/nat?lang=1  — all national acts, sorted newest-first.
Filters docs by fintech-relevant keywords in the document title.
Publishes to Kafka topic: reg.lex
"""

from __future__ import annotations

import logging
import re
import sys
import os
from typing import List
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base_worker import BaseWorker, DocMeta
import config

logger = logging.getLogger(__name__)

BASE_URL = "https://lex.uz"

# Fetch this many pages per run (20 docs/page → 400 docs max, newest first)
MAX_PAGES = 20

# Finance/banking classifier — entry point
SEARCH_URL = f"{BASE_URL}/ru/search/nat?lang=1"

FINTECH_KEYWORDS: list[tuple[str, str]] = [
    (r"(отмыван|финансир.{0,10}террор|легализац|пфм|aml|финмонитор)", "aml_cft"),
    (r"(платежн|перевод|электронн.?деньг|кошел[её]к|эквайринг|p2p|расчет)", "payments"),
    (r"(идентификац|верификац|kyc|надлежащ.?проверк)", "kyc"),
    (r"(персональн.?данн|приватност|защит.?персон)", "data_protection"),
    (r"(лицензи|допуск|разрешени.{0,15}(банк|платеж|финанс))", "licensing"),
    (r"(информационн.?безопасност|киберб|защит.?(информ|систем))", "cybersecurity"),
    (r"(потребител.{0,20}(защит|прав|кредит)|защит.?потребител)", "consumer_protection"),
    (r"(крипт|цифров.?актив|блокчейн|токен|виртуальн.?валют)", "crypto"),
    (r"(искусственн.?интеллект|скоринг|алгоритм.?(решени|кредит))", "ai_scoring"),
    (r"(отчетност|аудит|хранени.?данн|надзор.{0,15}(банк|финанс))", "reporting"),
    (r"(банк|небанк.?кредит|микрофинанс|микрокредит|ипотек)", "licensing"),
    (r"(валют|обмен.{0,10}валют|конвертац)", "payments"),
]


def _classify_title(title: str) -> str | None:
    t = title.lower()
    for pattern, category in FINTECH_KEYWORDS:
        if re.search(pattern, t):
            return category
    return None  # None = not fintech-relevant, skip


class LexWorker(BaseWorker):
    source_id   = "lex"
    kafka_topic = config.TOPIC_LEX
    s3_prefix   = "lex"

    def discover(self) -> List[DocMeta]:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        docs: List[DocMeta] = []
        seen: set[str] = set()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=config.HTTP_HEADERS.get("User-Agent", "Mozilla/5.0"),
                )
                page = context.new_page()

                logger.info("[lex] loading search page...")
                page.goto(SEARCH_URL, wait_until="networkidle", timeout=30_000)
                page.wait_for_timeout(2_000)  # extra settle time for ASP.NET

                for page_num in range(1, MAX_PAGES + 1):
                    page.wait_for_load_state("domcontentloaded")
                    soup = BeautifulSoup(page.content(), "lxml")
                    links = soup.find_all("a", href=re.compile(r"^/ru/docs/\-?\d+$"))

                    found = 0
                    for link in links:
                        href    = link["href"]
                        lex_id  = href.strip("/").split("/")[-1].lstrip("-")
                        doc_id  = f"lex-{lex_id}"
                        if doc_id in seen:
                            continue
                        seen.add(doc_id)

                        title    = link.get_text(strip=True) or doc_id
                        category = _classify_title(title)
                        if category is None:
                            continue  # skip non-fintech

                        docs.append(DocMeta(
                            doc_id     = doc_id,
                            name       = title,
                            source_url = urljoin(BASE_URL, href),
                            source_id  = self.source_id,
                            category   = category,
                        ))
                        found += 1

                    logger.info("[lex] page %d: %d links, %d fintech kept", page_num, len(links), found)

                    if page_num >= MAX_PAGES:
                        break

                    # Navigate to next page via __doPostBack pagination buttons
                    if not self._goto_next_page(page, page_num):
                        logger.info("[lex] no next page after page %d", page_num)
                        break
                    page.wait_for_timeout(1_500)  # wait for ASP.NET postback to settle

            finally:
                browser.close()

        logger.info("[lex] discovered %d fintech docs total", len(docs))
        return docs

    def _goto_next_page(self, page, current_page: int) -> bool:
        """Click the (current_page+1) button using __doPostBack."""
        from playwright.sync_api import TimeoutError as PWTimeout

        target_text = str(current_page + 1)

        # Find pagination buttons by class
        buttons = page.query_selector_all("a.btn_pgn_extend")
        for btn in buttons:
            if btn.text_content().strip() == target_text:
                href = btn.get_attribute("href") or ""
                m = re.search(r"__doPostBack\('([^']+)'", href)
                if m:
                    try:
                        page.evaluate(f"__doPostBack('{m.group(1)}', '')")
                        page.wait_for_load_state("networkidle", timeout=15_000)
                        return True
                    except PWTimeout:
                        return False

        # Fallback: try clicking any button with matching text
        try:
            btn = page.query_selector(f"a:has-text('{target_text}')")
            if btn:
                href = btn.get_attribute("href") or ""
                m = re.search(r"__doPostBack\('([^']+)'", href)
                if m:
                    page.evaluate(f"__doPostBack('{m.group(1)}', '')")
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    return True
        except Exception:
            pass

        return False

    def download(self, doc: DocMeta) -> bytes:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=config.HTTP_HEADERS.get("User-Agent", "Mozilla/5.0"),
                )
                page = context.new_page()
                page.goto(doc.source_url, wait_until="networkidle", timeout=30_000)
                soup = BeautifulSoup(page.content(), "lxml")

                # Try to find a PDF/DOCX download link
                for ext in ("pdf", "docx", "doc"):
                    link = soup.find("a", href=re.compile(rf"\.{ext}$", re.I))
                    if link:
                        file_url = urljoin(BASE_URL, link["href"])
                        try:
                            resp = context.request.get(file_url, timeout=30_000)
                            if resp.ok and resp.body():
                                return resp.body()
                        except Exception:
                            pass

                # Fallback: return full HTML content
                return page.content().encode("utf-8")
            finally:
                browser.close()

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
    LexWorker().run_loop(interval_hours=config.LEX_INTERVAL_HOURS)
