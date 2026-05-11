"""
CBU Worker — Central Bank of Uzbekistan
https://cbu.uz/ru/documents/

Scrapes all normative document categories.
Pagination uses PAGEN_1=N (Bitrix CMS style).
Publishes to Kafka topic: reg.cbu
"""

from __future__ import annotations

import logging
import re
import sys
import os
from typing import List
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base_worker import BaseWorker, DocMeta
import config

logger = logging.getLogger(__name__)

BASE_URL = "https://cbu.uz"

# All document categories on cbu.uz (discovered via Playwright)
CBU_CATEGORIES = {
    "3311": "licensing",    # Законы
    "3312": "licensing",    # Указы Президента
    "3313": "licensing",    # Постановления Президента
    "3314": "licensing",    # Постановления Кабинета Министров
    "3315": "licensing",    # Нормативные акты ЦБ  ← main source
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

            # Find document links: /ru/documents/{cat_code}/{doc_id}/
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
                ))

            # Check if next page exists via PAGEN_1=page+1 link
            next_link = soup.find("a", href=re.compile(rf"PAGEN_1={page + 1}"))
            if not next_link:
                logger.info("[cbu] cat=%s: last page is %d (%d docs)", cat_code, page, len(docs))
                break
            page += 1

        return docs

    def download(self, doc: DocMeta) -> bytes:
        file_url = self._find_file_url(doc.source_url)
        if file_url:
            return self._fetch_bytes(file_url)
        return self._fetch_html(doc.source_url).encode("utf-8")

    def _find_file_url(self, detail_url: str) -> str | None:
        try:
            html  = self._fetch_html(detail_url)
            soup  = BeautifulSoup(html, "lxml")
            for ext in (".pdf", ".docx", ".doc"):
                link = soup.find("a", href=re.compile(rf"\.{ext[1:]}$", re.IGNORECASE))
                if link:
                    return urljoin(BASE_URL, link["href"])
        except Exception:
            pass
        return None

    def _save_extra(self, doc: DocMeta) -> None:
        cat_code = doc.doc_id.split("-")[1] if "-" in doc.doc_id else ""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cbu.normative_acts (doc_id, cbu_category, title_ru, detail_url)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (doc.doc_id, cat_code, doc.name, doc.source_url),
            )

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
