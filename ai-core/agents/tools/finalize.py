"""`finalize_analysis` tool — the LAST tool the agent MUST call.

Why this exists:
  The agent used to produce a free-form JSON string in its final message,
  which routinely came back wrapped in markdown fences or with prose tacked
  on. By making the final result a *tool call* with a strict Pydantic
  args_schema, the LLM has to produce structured arguments that LangChain
  parses for us — no markdown fence stripping, no malformed JSON.

What it does internally:
  - Validates every `doc_links[]` against ES via `get_document` —
    catches hallucinated doc_ids (e.g. when the LLM made up "cbu-9999-12345").
  - Soft-verifies every quote with `verify_quote` — does substring lookup
    against the doc's full_text / rule fields.
  - Hard-fails on missing docs (returns ERROR → LLM retries with fixed
    doc_ids).
  - Soft-warns on unverified quotes (returns OK with a `warnings` field),
    because LLMs paraphrase frequently and an absolute substring match
    rejects too many otherwise-correct citations.

Caller-side: after `agent.ainvoke(...)`, find the last tool_call named
`finalize_analysis` in the message stream — its `args` ARE the final
response (already typed by Pydantic). See `agents/graph.py::extract_final_report`.
"""

from __future__ import annotations

import json
from typing import List

from langchain_core.tools import tool

from ._es import get_es
from .schemas import FinalReport, Severity      # noqa: F401  re-export for callers

ES_INDEX = "regtech-docs"


# ── helpers ────────────────────────────────────────────────────────────────

async def _doc_exists(doc_id: str) -> bool:
    if not doc_id:
        return False
    try:
        return bool(await get_es().exists(index=ES_INDEX, id=doc_id))
    except Exception:
        return False


async def _quote_verify(doc_id: str, quote: str) -> bool:
    """Soft substring check across full_text / rule.requirement / rule.title.
    Mirrors `verify_quote` from search.py but inlined to skip the JSON
    round-trip when this tool calls it 30+ times per finalize."""
    q = (quote or "").strip()
    if len(q) < 20:
        return False
    try:
        resp = await get_es().get(
            index=ES_INDEX, id=doc_id, source=["full_text", "rules"],
        )
    except Exception:
        return False
    src = resp["_source"]
    if q in (src.get("full_text") or ""):
        return True
    for r in src.get("rules") or []:
        if q in (r.get("requirement") or "") or q in (r.get("title") or ""):
            return True
    return False


# ── the tool ───────────────────────────────────────────────────────────────

@tool("finalize_analysis", args_schema=FinalReport)
async def finalize_analysis(**kwargs) -> str:
    """ОБЯЗАТЕЛЬНЫЙ финальный шаг — отправь сюда полный структурированный
    отчёт по фиче. Вызывай ровно ОДИН раз, когда:
      • выявил все регуляторные риски (через search_regulations / search_rules);
      • для каждого риска есть пункт чеклиста с конкретным action;
      • каждый пункт ссылается на реальный doc_id из индекса
        (тот что вернул search_regulations или get_document) — не выдумывай!;
      • в quotes — реальные подстроки из текста закона.

    Инструмент:
      1) Проверит что все doc_links указывают на реально существующие
         документы в Elasticsearch (если нет — вернёт ERROR, тогда исправь
         doc_ids и вызови снова).
      2) Подтянет квот-верификацию для каждой цитаты. Цитаты, которые не
         нашлись substring-матчем, попадут в warnings — это не блокер,
         но в идеале их надо заменить на точные строки из source-текста.
      3) Если всё OK — вернёт "OK" + JSON отчёта. Не вызывай больше
         finalize_analysis после успешного ответа.

    Аргументы — те же что в FinalReport (feature_summary, overall_risk,
    jira_comment_summary, domains[], documents_to_update[], red_flags[]).
    """
    # Pydantic уже отвалидировал поля; собираем обратно объект для удобства.
    try:
        report = FinalReport(**kwargs)
    except Exception as exc:
        return f"ERROR: invalid report structure: {exc}"

    # 1) Все ли doc_ids существуют?
    missing_docs: List[str] = []
    seen: set[str] = set()
    for d in report.domains:
        for item in d.checklist:
            for did in item.doc_links:
                if did in seen:
                    continue
                seen.add(did)
                if not await _doc_exists(did):
                    missing_docs.append(did)

    if missing_docs:
        return (
            f"ERROR: doc_id(s) not found in index: {missing_docs}. "
            f"Используй только doc_id, которые вернули search_regulations/"
            f"search_rules/get_document. Исправь и вызови finalize_analysis "
            f"повторно."
        )

    # 2) Soft-verify quotes
    warnings: List[str] = []
    for di, d in enumerate(report.domains):
        for ci, item in enumerate(d.checklist):
            for qi, quote in enumerate(item.quotes):
                ok = False
                for did in item.doc_links:
                    if await _quote_verify(did, quote):
                        ok = True
                        break
                if not ok:
                    warnings.append(
                        f"unverified quote in domains[{di}].checklist[{ci}].quotes[{qi}] "
                        f"(domain={d.domain!r}): {quote[:80]!r}"
                    )

    payload = {
        "status":   "OK",
        "warnings": warnings,
        "report":   report.model_dump(),
    }
    return json.dumps(payload, ensure_ascii=False)
