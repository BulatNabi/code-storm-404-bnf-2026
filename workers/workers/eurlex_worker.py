"""
EUR-Lex Worker — European Union Regulations
https://eur-lex.europa.eu/

Monitors consolidated versions of key EU fintech regulations.
Uses known CELEX numbers — no scraping needed, stable URLs.
Publishes to Kafka topic: reg.eurlex
"""

from __future__ import annotations

import logging
import sys
import os
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from base_worker import BaseWorker, DocMeta
import config

logger = logging.getLogger(__name__)

BASE_URL = "https://eur-lex.europa.eu"


@dataclass
class EuRegulation:
    code:           str         # short code: GDPR, PSD2, etc.
    celex:          str         # EUR-Lex CELEX number
    official_ref:   str         # e.g. "EU 2016/679"
    name:           str
    category:       str
    language:       str = "en"  # EUR-Lex lang code


# ── Catalogue of EU regulations relevant to fintech ─────────────────────────
EU_REGULATIONS: List[EuRegulation] = [
    # ── Data Protection ──────────────────────────────────────────────────────
    EuRegulation(
        code="GDPR",          celex="32016R0679",   official_ref="EU 2016/679",
        name="General Data Protection Regulation (GDPR)",
        category="data_protection",
    ),
    # ── Payments ─────────────────────────────────────────────────────────────
    EuRegulation(
        code="PSD2",          celex="32015L2366",   official_ref="EU 2015/2366",
        name="Payment Services Directive 2 (PSD2)",
        category="payments",
    ),
    EuRegulation(
        code="PSD3",          celex="32024L2853",   official_ref="EU 2024/2853",
        name="Payment Services Directive 3 (PSD3)",
        category="payments",
    ),
    EuRegulation(
        code="SEPA_REG",      celex="32012R0260",   official_ref="EU 260/2012",
        name="SEPA Regulation (credit transfers and direct debits)",
        category="payments",
    ),
    # ── AML/CFT ──────────────────────────────────────────────────────────────
    EuRegulation(
        code="AMLD5",         celex="32018L0843",   official_ref="EU 2018/843",
        name="5th Anti-Money Laundering Directive (AMLD5)",
        category="aml_cft",
    ),
    EuRegulation(
        code="AMLD6",         celex="32018L1673",   official_ref="EU 2018/1673",
        name="6th Anti-Money Laundering Directive (AMLD6)",
        category="aml_cft",
    ),
    EuRegulation(
        code="TFR",           celex="32023R1113",   official_ref="EU 2023/1113",
        name="Transfer of Funds Regulation — wire transfer traceability",
        category="aml_cft",
    ),
    # ── Crypto ───────────────────────────────────────────────────────────────
    EuRegulation(
        code="MICA",          celex="32023R1114",   official_ref="EU 2023/1114",
        name="Markets in Crypto-Assets Regulation (MiCA)",
        category="crypto",
    ),
    # ── Cybersecurity / Operational Resilience ───────────────────────────────
    EuRegulation(
        code="DORA",          celex="32022R2554",   official_ref="EU 2022/2554",
        name="Digital Operational Resilience Act (DORA)",
        category="cybersecurity",
    ),
    EuRegulation(
        code="NIS2",          celex="32022L2555",   official_ref="EU 2022/2555",
        name="Network and Information Systems Directive 2 (NIS2)",
        category="cybersecurity",
    ),
    # ── AI & Algorithmic Decisions ────────────────────────────────────────────
    EuRegulation(
        code="AI_ACT",        celex="32024R1689",   official_ref="EU 2024/1689",
        name="EU Artificial Intelligence Act",
        category="ai_scoring",
    ),
    # ── Consumer Protection ───────────────────────────────────────────────────
    EuRegulation(
        code="CCD2",          celex="32023L2225",   official_ref="EU 2023/2225",
        name="Consumer Credit Directive 2 (CCD2)",
        category="consumer_protection",
    ),
    # ── eIDAS / Digital Identity ──────────────────────────────────────────────
    EuRegulation(
        code="EIDAS2",        celex="32024R1183",   official_ref="EU 2024/1183",
        name="eIDAS 2 — European Digital Identity Framework",
        category="kyc",
    ),
]


def _pdf_url(celex: str, lang: str = "EN") -> str:
    return f"{BASE_URL}/legal-content/{lang}/TXT/PDF/?uri=CELEX:{celex}"

def _html_url(celex: str, lang: str = "EN") -> str:
    return f"{BASE_URL}/legal-content/{lang}/TXT/HTML/?uri=CELEX:{celex}"


class EurLexWorker(BaseWorker):
    source_id   = "eurlex"
    kafka_topic = config.TOPIC_EURLEX
    s3_prefix   = "eurlex"

    def discover(self) -> List[DocMeta]:
        docs = []
        for reg in EU_REGULATIONS:
            doc_id = f"eurlex-{reg.celex}"
            docs.append(DocMeta(
                doc_id     = doc_id,
                name       = reg.name,
                source_url = _pdf_url(reg.celex, reg.language.upper()),
                source_id  = self.source_id,
                category   = reg.category,
                language   = reg.language,
                extra      = {
                    "celex":        reg.celex,
                    "code":         reg.code,
                    "official_ref": reg.official_ref,
                },
            ))
        logger.info("[eurlex] %d regulations to check", len(docs))
        return docs

    def download(self, doc: DocMeta) -> bytes:
        celex = doc.extra["celex"]
        lang  = doc.language.upper()

        # Try PDF first
        pdf_url = _pdf_url(celex, lang)
        try:
            content = self._fetch_bytes(pdf_url)
            if len(content) > 1024:
                logger.debug("[eurlex] downloaded PDF for %s (%d bytes)", celex, len(content))
                return content
        except Exception as exc:
            logger.warning("[eurlex] PDF failed for %s: %s — trying HTML", celex, exc)

        # Fallback: HTML version
        html_url = _html_url(celex, lang)
        content = self._fetch_bytes(html_url)
        logger.debug("[eurlex] downloaded HTML for %s (%d bytes)", celex, len(content))
        return content

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
                    doc.extra.get("code"),
                    doc.name,
                    doc.extra.get("official_ref"),
                    date.today(),
                    doc.source_url,
                ),
            )

    def _fetch_bytes(self, url: str) -> bytes:
        with httpx.Client(headers=config.HTTP_HEADERS, timeout=config.HTTP_TIMEOUT,
                          follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.content


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    EurLexWorker().run_loop(interval_hours=config.EURLEX_INTERVAL_HOURS)
