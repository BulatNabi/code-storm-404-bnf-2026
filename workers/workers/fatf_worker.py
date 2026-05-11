"""
FATF Worker — Financial Action Task Force
https://www.fatf-gafi.org/

Downloads FATF recommendations, guidance, and the Uzbekistan mutual evaluation report.
Publishes to Kafka topic: reg.fatf
"""

from __future__ import annotations

import logging
import sys
import os
from dataclasses import dataclass
from typing import List

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base_worker import BaseWorker, DocMeta
import config

logger = logging.getLogger(__name__)

BASE = "https://www.fatf-gafi.org"


@dataclass
class FatfDoc:
    doc_id:      str
    name:        str
    pdf_url:     str
    category:    str
    report_type: str          # recommendations | mutual_eval | guidance | typologies
    country:     str = ""     # ISO-2 or ""


# ── FATF document catalogue ──────────────────────────────────────────────────
FATF_DOCS: List[FatfDoc] = [
    # ── Core AML/CFT Standards ───────────────────────────────────────────────
    FatfDoc(
        doc_id      = "fatf-40-recommendations-2012",
        name        = "FATF 40 Recommendations (2012, updated 2024)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/recommendations/FATF%20Recommendations%202012.pdf",
        category    = "aml_cft",
        report_type = "recommendations",
    ),
    FatfDoc(
        doc_id      = "fatf-methodology-2013",
        name        = "FATF Methodology for Assessing Technical Compliance (2013)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/methodology/FATF%20Methodology%2022%20Feb%202013.pdf",
        category    = "aml_cft",
        report_type = "recommendations",
    ),
    # ── Uzbekistan Mutual Evaluation ─────────────────────────────────────────
    FatfDoc(
        doc_id      = "fatf-mer-uzbekistan-2022",
        name        = "FATF Mutual Evaluation Report — Uzbekistan (2022)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/mer/Mutual-Evaluation-Report-Uzbekistan-2022.pdf",
        category    = "aml_cft",
        report_type = "mutual_eval",
        country     = "UZ",
    ),
    # ── Virtual Assets / Crypto ───────────────────────────────────────────────
    FatfDoc(
        doc_id      = "fatf-guidance-virtual-assets-2021",
        name        = "FATF Updated Guidance for Virtual Assets and VASPs (2021)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/guidance/Updated-Guidance-VA-VASP.pdf",
        category    = "crypto",
        report_type = "guidance",
    ),
    # ── Digital Identity ──────────────────────────────────────────────────────
    FatfDoc(
        doc_id      = "fatf-guidance-digital-identity-2020",
        name        = "FATF Guidance on Digital Identity (2020)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/guidance/Guidance-on-Digital-Identity.pdf",
        category    = "kyc",
        report_type = "guidance",
    ),
    # ── Payments / Wire Transfers ─────────────────────────────────────────────
    FatfDoc(
        doc_id      = "fatf-guidance-wire-transfers-2016",
        name        = "FATF Guidance on the Traveller Rule / Wire Transfers (2016)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/guidance/16-INF-Wire-Transfers.pdf",
        category    = "payments",
        report_type = "guidance",
    ),
    # ── Fintech & Regtech ─────────────────────────────────────────────────────
    FatfDoc(
        doc_id      = "fatf-guidance-regtech-2021",
        name        = "FATF Guidance on Opportunities and Challenges of New Technologies (Regtech, 2021)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/guidance/Opportunities-Challenges-of-New-Technologies.pdf",
        category    = "aml_cft",
        report_type = "guidance",
    ),
    # ── Correspondent Banking ─────────────────────────────────────────────────
    FatfDoc(
        doc_id      = "fatf-guidance-correspondent-banking-2016",
        name        = "FATF Guidance on Correspondent Banking (2016)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/guidance/Correspondent-Banking-Services.pdf",
        category    = "payments",
        report_type = "guidance",
    ),
    # ── PEP / Sanctions ──────────────────────────────────────────────────────
    FatfDoc(
        doc_id      = "fatf-guidance-pep-2013",
        name        = "FATF Guidance on Politically Exposed Persons (PEPs, 2013)",
        pdf_url     = f"{BASE}/content/dam/fatf-gafi/guidance/Guidance-PEP-Rec12-22.pdf",
        category    = "aml_cft",
        report_type = "guidance",
    ),
]


class FatfWorker(BaseWorker):
    source_id   = "fatf"
    kafka_topic = config.TOPIC_FATF
    s3_prefix   = "fatf"

    def discover(self) -> List[DocMeta]:
        docs = []
        for fd in FATF_DOCS:
            docs.append(DocMeta(
                doc_id     = fd.doc_id,
                name       = fd.name,
                source_url = fd.pdf_url,
                source_id  = self.source_id,
                category   = fd.category,
                language   = "en",
                extra      = {
                    "report_type": fd.report_type,
                    "country":     fd.country,
                },
            ))
        logger.info("[fatf] %d documents to check", len(docs))
        return docs

    def download(self, doc: DocMeta) -> bytes:
        headers = {
            **config.HTTP_HEADERS,
            "Referer": "https://www.fatf-gafi.org/",
            "Accept": "application/pdf,*/*",
        }
        with httpx.Client(headers=headers, timeout=config.HTTP_TIMEOUT,
                          follow_redirects=True) as client:
            r = client.get(doc.source_url)
            if r.status_code == 403:
                # FATF blocks hot-linking; fetch the HTML page first to get a valid session
                page_url = doc.source_url.replace("/content/dam/fatf-gafi/", "/en/publications/").replace(".pdf", ".html")
                try:
                    client.get("https://www.fatf-gafi.org/")  # warm session
                except Exception:
                    pass
                r = client.get(doc.source_url, headers={**headers, "Referer": "https://www.fatf-gafi.org/en/publications/"})
            r.raise_for_status()
            return r.content

    def _save_extra(self, doc: DocMeta) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fatf.reports
                    (doc_id, report_type, country_code, title, pdf_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    doc.doc_id,
                    doc.extra.get("report_type"),
                    doc.extra.get("country") or None,
                    doc.name,
                    doc.source_url,
                ),
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    FatfWorker().run_loop(interval_hours=config.FATF_INTERVAL_HOURS)
