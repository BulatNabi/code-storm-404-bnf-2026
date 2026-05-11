"""
Lex Worker — Lex.uz National Legislation of Uzbekistan
https://lex.uz/ru/

Watches for new/updated laws and presidential decrees relevant to fintech.
Publishes to Kafka topic: reg.lex
"""

from __future__ import annotations

import logging
import re
import sys
import os
from typing import List
from urllib.parse import urljoin, urlencode

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base_worker import BaseWorker, DocMeta
import config

logger = logging.getLogger(__name__)

BASE_URL = "https://lex.uz"

# Document form IDs on lex.uz (from their filter system)
# 1=Конституция, 2=Кодекс, 3=Закон, 4=Указ Президента, 5=Постановление Президента,
# 6=Постановление Кабинета Министров, 7=Приказ министерства
LEX_FORMS = [3, 4, 5]  # Laws, Presidential Decrees, Presidential Resolutions

# We search by keywords relevant to fintech
FINTECH_QUERIES = [
    ("банк",           "licensing"),
    ("платеж",         "payments"),
    ("электронн",      "payments"),
    ("персональн данн","data_protection"),
    ("финансов монитор","aml_cft"),
    ("отмыван",        "aml_cft"),
    ("идентификац",    "kyc"),
    ("крипт",          "crypto"),
    ("потребител",     "consumer_protection"),
    ("информационн безопасност", "cybersecurity"),
]


class LexWorker(BaseWorker):
    source_id   = "lex"
    kafka_topic = config.TOPIC_LEX
    s3_prefix   = "lex"

    def discover(self) -> List[DocMeta]:
        seen: set[str] = set()
        docs: List[DocMeta] = []

        for query, category in FINTECH_QUERIES:
            for form_id in LEX_FORMS:
                results = self._search(query, form_id, category)
                for doc in results:
                    if doc.doc_id not in seen:
                        seen.add(doc.doc_id)
                        docs.append(doc)

        logger.info("[lex] total unique docs discovered: %d", len(docs))
        return docs

    def _search(self, query: str, form_id: int, category: str) -> List[DocMeta]:
        docs: List[DocMeta] = []
        page = 1
        while True:
            params = {
                "q":           query,
                "form":        form_id,
                "status":      1,     # active only
                "page":        page,
                "per_page":    50,
            }
            url = f"{BASE_URL}/ru/search/?" + urlencode(params)
            try:
                html = self._fetch(url)
            except Exception as exc:
                logger.error("[lex] search failed for q=%r form=%s: %s", query, form_id, exc)
                break

            soup  = BeautifulSoup(html, "lxml")
            items = soup.select(".search-result__item, .result-item, li.doc-result")

            if not items:
                # Fallback: find any lex.uz document links
                links = soup.find_all("a", href=re.compile(r"/ru/docs/\w+"))
                for link in links:
                    href = link["href"]
                    lex_id = href.strip("/").split("/")[-1]
                    doc_id = f"lex-{lex_id}"
                    title  = link.get_text(strip=True) or doc_id
                    docs.append(DocMeta(
                        doc_id     = doc_id,
                        name       = title,
                        source_url = urljoin(BASE_URL, href),
                        source_id  = self.source_id,
                        category   = category,
                        extra      = {"query": query, "form_id": form_id},
                    ))
                if not links or page >= 10:
                    break
                page += 1
                continue

            for item in items:
                link = item.find("a", href=True)
                if not link:
                    continue
                lex_id = link["href"].strip("/").split("/")[-1]
                doc_id = f"lex-{lex_id}"
                title  = link.get_text(strip=True) or doc_id

                # Try to extract act number (e.g. ЗРУ-547)
                act_number_match = re.search(r"[ЗПУ]{1,3}РУ-\d+", item.get_text())
                extra = {
                    "query":      query,
                    "form_id":    form_id,
                    "act_number": act_number_match.group(0) if act_number_match else "",
                }
                docs.append(DocMeta(
                    doc_id     = doc_id,
                    name       = title,
                    source_url = urljoin(BASE_URL, link["href"]),
                    source_id  = self.source_id,
                    category   = category,
                    extra      = extra,
                ))

            next_page = soup.select_one("a[rel='next'], .pagination .next")
            if not next_page or page >= 10:
                break
            page += 1

        return docs

    def download(self, doc: DocMeta) -> bytes:
        html = self._fetch(doc.source_url)
        soup = BeautifulSoup(html, "lxml")

        # Look for PDF/DOCX download link first
        for ext in ("pdf", "docx", "doc"):
            link = soup.find("a", href=re.compile(rf"\.{ext}$", re.I))
            if link:
                file_url = urljoin(BASE_URL, link["href"])
                try:
                    return self._fetch_bytes(file_url)
                except Exception:
                    pass

        # Fallback: save full page HTML (lex.uz documents are often HTML-only)
        return html.encode("utf-8")

    def _save_extra(self, doc: DocMeta) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lex.acts (doc_id, lex_id, act_number, act_type, title_ru, detail_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (lex_id) DO UPDATE
                    SET title_ru = EXCLUDED.title_ru
                """,
                (
                    doc.doc_id,
                    doc.doc_id.replace("lex-", ""),
                    doc.extra.get("act_number", ""),
                    _form_name(doc.extra.get("form_id")),
                    doc.name,
                    doc.source_url,
                ),
            )

    def _fetch(self, url: str) -> str:
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


def _form_name(form_id) -> str:
    return {3: "Закон", 4: "Указ Президента", 5: "Постановление Президента"}.get(
        int(form_id) if form_id else 0, "Акт"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    LexWorker().run_loop(interval_hours=config.LEX_INTERVAL_HOURS)
