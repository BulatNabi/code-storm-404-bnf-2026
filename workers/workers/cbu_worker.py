"""
CBU Worker — Central Bank of Uzbekistan
https://cbu.uz/ru/documents/

Each CBU detail page is just a stub of metadata that links out to lex.uz
where the actual normative-act text lives. So:

  discover()  walks the CBU listing pages and yields one DocMeta per item,
              capturing the CBU registry metadata in `extra`.
  download()  follows the lex.uz link on the detail page and renders the
              law text through the shared Playwright session. If no lex
              link is present, falls back to the detail HTML.

Publishes to Kafka topic: reg.cbu
"""

from __future__ import annotations

import logging
import re
import sys
import os
from typing import List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base_worker import BaseWorker, DocMeta
import config

logger = logging.getLogger(__name__)

BASE_URL = "https://cbu.uz"

# All document categories on cbu.uz (probed against the live index).
# Default category is a starting hint; the per-title classifier may override it.
CBU_CATEGORIES = {
    "3311": "licensing",            # Законы                                       (~60 docs)
    "3312": "licensing",            # Указы Президента                             (~90 docs)
    "3313": "licensing",            # Постановления Президента                     (~135 docs)
    "3314": "licensing",            # Постановления Кабинета Министров             (~30 docs)
    "3315": "licensing",            # Нормативные акты ЦБ  ← main source           (~360 docs)
    "3316": "reporting",            # Комментарии к нормативным актам              (~30 docs)
    "3317": "licensing",            # Кодекс профессиональной этики                (1 doc)
    "3344": "consumer_protection",  # Невмешательство в предпринимательскую деят.  (~5 docs)
}

TITLE_KEYWORDS: list[tuple[str, str]] = [
    (r"(отмыван|финмонитор|легализац|пфм|aml|подозрительн)", "aml_cft"),
    (r"(платежн|перевод|электронн.?деньг|кошел[её]к|эквайринг|p2p)", "payments"),
    (r"(идентификац|верификац|kyc|надлежащ.?проверк|onboarding)", "kyc"),
    (r"(персональн.?данн|приватност)", "data_protection"),
    (r"(лицензи|допуск|разрешени|статус.?(банк|организац))", "licensing"),
    (r"(информационн.?безопасност|киберб|защит.?(информ|систем)|dora)", "cybersecurity"),
    (r"(потребител|раскрыти|жалоб|претензи)", "consumer_protection"),
    (r"(крипт|цифров.?актив|блокчейн|токен|виртуальн.?валют)", "crypto"),
    (r"(искусственн.?интеллект|ai|скоринг|алгоритм.?(решени|кредит))", "ai_scoring"),
    (r"(отчетност|аудит|хранени.?данн|надзор|мониторинг)", "reporting"),
]

LEX_HREF_RE = re.compile(r"lex\.uz/(?:[a-z]{2}/)?docs?/(\d+)", re.I)


def _classify_title(title: str) -> str:
    t = title.lower()
    for pattern, category in TITLE_KEYWORDS:
        if re.search(pattern, t):
            return category
    return "licensing"


class CBUWorker(BaseWorker):
    source_id   = "cbu"
    kafka_topic = config.TOPIC_CBU
    s3_prefix   = "cbu"

    def discover(self) -> List[DocMeta]:
        docs: List[DocMeta] = []
        seen: set[str] = set()
        for cat_code, default_cat in CBU_CATEGORIES.items():
            for doc in self._scrape_category(cat_code, default_cat):
                if doc.doc_id not in seen:
                    seen.add(doc.doc_id)
                    docs.append(doc)
        return docs

    def _scrape_category(self, cat_code: str, default_category: str) -> List[DocMeta]:
        docs: List[DocMeta] = []
        page = 1

        while page <= 50:
            url = f"{BASE_URL}/ru/documents/{cat_code}/?PAGEN_1={page}"
            try:
                html = self._fetch_html(url)
            except Exception as exc:
                logger.error("[cbu] fetch failed %s: %s", url, exc)
                break

            soup = BeautifulSoup(html, "lxml")
            links = soup.find_all(
                "a",
                href=re.compile(rf"^/ru/documents/{cat_code}/\d+/?$"),
            )

            if not links:
                logger.info("[cbu] cat=%s page=%d: no docs, stopping", cat_code, page)
                break

            for link in links:
                raw_id = link["href"].strip("/").split("/")[-1]
                if not raw_id.isdigit():
                    continue
                doc_id = f"cbu-{cat_code}-{raw_id}"
                title  = link.get_text(strip=True) or doc_id
                docs.append(DocMeta(
                    doc_id     = doc_id,
                    name       = title,
                    source_url = urljoin(BASE_URL, link["href"]),
                    source_id  = self.source_id,
                    category   = _classify_title(title),
                    extra      = {"cbu_category": cat_code},
                ))

            next_link = soup.find("a", href=re.compile(rf"PAGEN_1={page + 1}"))
            if not next_link:
                logger.info("[cbu] cat=%s: last page is %d (%d docs)", cat_code, page, len(docs))
                break
            page += 1

        return docs

    def download(self, doc: DocMeta) -> bytes:
        """Follow the lex.uz link on the CBU detail page and render the actual law text."""
        try:
            detail_html = self._fetch_html(doc.source_url)
        except Exception as exc:
            logger.error("[cbu] failed to fetch detail page %s: %s", doc.source_url, exc)
            return b""

        # Stash registry metadata for _save_extra
        doc.extra.update(self._parse_meta(detail_html))

        lex_url = self._extract_lex_url(detail_html)
        if not lex_url:
            logger.warning("[cbu] no lex.uz link on %s — falling back to detail HTML", doc.source_url)
            return detail_html.encode("utf-8")

        doc.extra["lex_url"] = lex_url

        # Make sure Playwright has a clean lex.uz session before we navigate to the doc.
        self._playwright_warm_up("https://lex.uz/ru/")
        try:
            content = self._playwright_fetch(lex_url, settle_ms=2_000, timeout_ms=45_000)
            if len(content) < 5_000:
                logger.warning("[cbu] lex.uz returned %d bytes for %s — using detail HTML",
                               len(content), doc.doc_id)
                return detail_html.encode("utf-8")
            return content
        except Exception as exc:
            logger.error("[cbu] playwright fetch %s failed: %s", lex_url, exc)
            return detail_html.encode("utf-8")

    def _extract_lex_url(self, detail_html: str) -> Optional[str]:
        soup = BeautifulSoup(detail_html, "lxml")
        for a in soup.find_all("a", href=True):
            m = LEX_HREF_RE.search(a["href"])
            if m:
                return f"https://lex.uz/ru/docs/{m.group(1)}"
        return None

    def _parse_meta(self, detail_html: str) -> dict:
        """Extract registry metadata (CB & MoJ numbers and dates) from the CBU detail page."""
        text = BeautifulSoup(detail_html, "lxml").get_text(" ", strip=True)
        out: dict = {}
        for label, key in [
            (r"№ в ЦБ",   "cbu_doc_number"),
            (r"№ в МЮ",   "moj_doc_number"),
            (r"Дата в ЦБ","cbu_date"),
            (r"Дата в МЮ","moj_date"),
        ]:
            m = re.search(rf"{label}:\s*([^\s][^\n]*?)(?=\s+(?:№|Дата|lex\.uz|Поделиться|Назад)|$)",
                          text)
            if m:
                out[key] = m.group(1).strip()
        return out

    def _save_extra(self, doc: DocMeta) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cbu.normative_acts
                    (doc_id, cbu_category, cbu_doc_number, title_ru, detail_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    doc.doc_id,
                    doc.extra.get("cbu_category", ""),
                    doc.extra.get("cbu_doc_number"),
                    doc.name,
                    doc.source_url,
                ),
            )

    def _fetch_html(self, url: str) -> str:
        with httpx.Client(headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT,
                          follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    CBUWorker().run_once()
