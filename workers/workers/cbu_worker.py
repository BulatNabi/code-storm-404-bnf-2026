"""
CBU Worker — Central Bank of Uzbekistan
https://cbu.uz/ru/documents/

Fetches normative acts from CBU category pages.
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

# CBU document category codes → fintech-relevant categories
CBU_CATEGORIES = {
    "3315": None,  # Нормативные акты ЦБ (all, we classify by title)
    "3311": None,  # Законы (banking laws)
}

# Keyword → doc category mapping (applied to document title)
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

BASE_URL = "https://cbu.uz"


def _classify_title(title: str) -> str:
    t = title.lower()
    for pattern, category in TITLE_KEYWORDS:
        if re.search(pattern, t):
            return category
    return "licensing"  # default for CBU acts


class CBUWorker(BaseWorker):
    source_id   = "cbu"
    kafka_topic = config.TOPIC_CBU
    s3_prefix   = "cbu"

    def discover(self) -> List[DocMeta]:
        docs: List[DocMeta] = []
        for cat_code in CBU_CATEGORIES:
            docs.extend(self._scrape_category(cat_code))
        return docs

    def _scrape_category(self, cat_code: str) -> List[DocMeta]:
        docs: List[DocMeta] = []
        page = 1
        while True:
            url = f"{BASE_URL}/ru/documents/{cat_code}/?page={page}"
            try:
                html = self._fetch_html(url)
            except Exception as exc:
                logger.error("[cbu] failed to fetch listing %s: %s", url, exc)
                break

            soup  = BeautifulSoup(html, "lxml")
            items = soup.select("div.documents-list__item, li.doc-item, .document-item")

            # Fallback: any link with /ru/documents/{cat}/ pattern
            if not items:
                links = soup.find_all("a", href=re.compile(rf"/ru/documents/{cat_code}/\d+"))
                for link in links:
                    detail_url = urljoin(BASE_URL, link["href"])
                    doc_id     = f"cbu-{cat_code}-{link['href'].strip('/').split('/')[-1]}"
                    title      = link.get_text(strip=True) or doc_id
                    docs.append(DocMeta(
                        doc_id     = doc_id,
                        name       = title,
                        source_url = detail_url,
                        source_id  = self.source_id,
                        category   = _classify_title(title),
                    ))
                if not links:
                    break
                page += 1
                if page > 50:
                    break
                continue

            for item in items:
                link = item.find("a", href=True)
                if not link:
                    continue
                detail_url = urljoin(BASE_URL, link["href"])
                raw_id     = link["href"].strip("/").split("/")[-1]
                doc_id     = f"cbu-{cat_code}-{raw_id}"
                title      = link.get_text(strip=True) or doc_id
                docs.append(DocMeta(
                    doc_id     = doc_id,
                    name       = title,
                    source_url = detail_url,
                    source_id  = self.source_id,
                    category   = _classify_title(title),
                ))

            # Check if there is a next page
            next_btn = soup.select_one("a[rel='next'], .pagination__next, a.next")
            if not next_btn:
                break
            page += 1
            if page > 50:
                break

        logger.info("[cbu] category %s: %d docs discovered", cat_code, len(docs))
        return docs

    def download(self, doc: DocMeta) -> bytes:
        # First try to find a direct file link on the detail page
        file_url = self._find_file_url(doc.source_url)
        if file_url:
            return self._fetch_bytes(file_url)
        # Fallback: save the HTML of the detail page itself
        return self._fetch_html(doc.source_url).encode("utf-8")

    def _find_file_url(self, detail_url: str) -> Optional[str]:
        try:
            html = self._fetch_html(detail_url)
        except Exception:
            return None
        soup  = BeautifulSoup(html, "lxml")
        for ext in (".pdf", ".docx", ".doc"):
            link = soup.find("a", href=re.compile(rf"\.{ext[1:]}$", re.IGNORECASE))
            if link:
                return urljoin(BASE_URL, link["href"])
        return None

    def _save_extra(self, doc: DocMeta) -> None:
        cat_code = doc.doc_id.split("-")[1] if "-" in doc.doc_id else ""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cbu.normative_acts
                    (doc_id, cbu_category, title_ru, detail_url)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (doc.doc_id, cat_code, doc.name, doc.source_url),
            )

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    def _fetch_html(self, url: str) -> str:
        with httpx.Client(headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT,
                          follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.text

    def _fetch_bytes(self, url: str) -> bytes:
        with httpx.Client(headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT,
                          follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.content


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    CBUWorker().run_loop(interval_hours=config.CBU_INTERVAL_HOURS)
