"""FastAPI server for the RegTech AI agent.

Endpoints:
  POST /api/v1/analyze
      Synchronous. Runs the full agent, returns the final structured
      report or an error.

  POST /api/v1/analyze/stream
      Server-Sent Events. Streams every reasoning step the agent takes:
      tool calls, intermediate observations, and the final report. The
      front-end can render each event as it arrives ("анализирует
      регулирование → найдено 5 документов → формирует чеклист → готово").

  GET  /api/v1/health
      Cheap status check. Includes ES connectivity probe.

  GET  /api/v1/tags
      Convenience: passes through to list_available_tags() so the UI can
      show a tag-picker without round-tripping the agent.

Run locally:
  uvicorn agents.main:app --host 0.0.0.0 --port 8001 --reload

Env required:
  ES_URL                       (default http://localhost:9200)
  ES_INDEX                     (default regtech-docs)
  LLM_API_KEY                  OpenRouter / OpenAI key
  LLM_BASE_URL                 (default https://openrouter.ai/api/v1)
  EMBEDDING_MODEL              (default openai/text-embedding-3-small)
  EMBEDDING_DIMS               (default 1536)
  AGENT_MODEL                  (default openai/gpt-4o-mini)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, Field

from .graph import build_agent, extract_final_report_or_raw
from .tools._es import close_es, get_es
from .tools.search import list_available_tags


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the agent once on boot — saves a few hundred ms per request."""
    app.state.agent = build_agent()
    yield
    await close_es()


app = FastAPI(
    title="RegTech AI Core",
    version="0.1.0",
    description="Финтех-регуляторный радар — Track 4. Принимает описание "
                "фичи, возвращает структурированный отчёт о регуляторных "
                "рисках с ссылками на конкретные статьи НПА.",
    lifespan=lifespan,
)

# CORS — фронт на :3000 должен достучаться без боли
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── request / response models ──────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    feature_description: str = Field(
        ...,
        description="Описание фичи / user story / свободный текст",
        min_length=10,
    )
    session_id: Optional[str] = Field(
        None,
        description="Опциональный thread_id для продолжения предыдущего "
                    "разговора (агент помнит контекст через MemorySaver).",
    )


class AnalyzeResponse(BaseModel):
    session_id: str
    source: str                  # "finalize_tool" | "raw_json_fallback" | "unparsed"
    report:  Optional[dict] = None
    raw:     Optional[str]  = None
    error:   Optional[str]  = None
    latency_s: float
    tool_calls: int = 0


# ── endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/v1/health")
async def health() -> dict:
    es_status = "unknown"
    try:
        info = await get_es().info()
        es_status = info.get("cluster_name", "ok") or "ok"
    except Exception as exc:
        es_status = f"error: {exc}"
    return {
        "service":         "ai-core",
        "agent_model":     os.environ.get("AGENT_MODEL", "openai/gpt-4o-mini"),
        "embedding_model": os.environ.get("EMBEDDING_MODEL", "openai/text-embedding-3-small"),
        "es_cluster":      es_status,
        "es_index":        os.environ.get("ES_INDEX", "regtech-docs"),
    }


@app.get("/api/v1/tags")
async def tags() -> dict:
    """Pass-through for the agent's `list_available_tags` tool, so the UI
    can populate a tag-filter without invoking the full agent."""
    raw = await list_available_tags.ainvoke({})
    try:
        return {"tags": json.loads(raw)}
    except Exception:
        return {"tags": [], "error": "could not parse"}


@app.post("/api/v1/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """Synchronous analysis. Returns the final report or an error.
    Wait time on the populated index ≈ 3–8 sec depending on model and how
    many tool round-trips the agent decides to make."""
    session_id = req.session_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": session_id}}
    started = time.perf_counter()

    try:
        # recursion_limit caps the ReAct loop at ~10 tool roundtrips.
        # Without this, a confused LLM can burn 20+ minutes spamming
        # search_regulations with empty-result tag combinations.
        result = await app.state.agent.ainvoke(
            {"messages": [("user", req.feature_description)]},
            config={**config, "recursion_limit": 20},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    messages: list[BaseMessage] = result.get("messages") or []
    extracted = extract_final_report_or_raw(messages)

    tool_calls = sum(
        len(getattr(m, "tool_calls", []) or [])
        for m in messages
        if isinstance(m, AIMessage)
    )

    return AnalyzeResponse(
        session_id = session_id,
        source     = extracted.get("source") or "unknown",
        report     = extracted.get("report"),
        raw        = extracted.get("raw"),
        error      = extracted.get("error"),
        latency_s  = round(time.perf_counter() - started, 3),
        tool_calls = tool_calls,
    )


@app.post("/api/v1/analyze/stream")
async def analyze_stream(req: AnalyzeRequest) -> StreamingResponse:
    """SSE streaming endpoint. Emits one event per agent step:
      • event: started        data: {"session_id": "..."}
      • event: tool_call      data: {"name": "search_regulations", "args": {...}}
      • event: tool_result    data: {"name": "search_regulations", "preview": "..."}
      • event: ai_message     data: {"content": "..."}      (rare — usually empty mid-loop)
      • event: final          data: {<AnalyzeResponse JSON>}
      • event: error          data: {"detail": "..."}

    Frontend consumes via `new EventSource('/api/v1/analyze/stream')`.
    """
    session_id = req.session_id or str(uuid.uuid4())

    async def event_stream() -> AsyncIterator[bytes]:
        config = {"configurable": {"thread_id": session_id}}
        started = time.perf_counter()
        tool_calls = 0

        yield _sse("started", {"session_id": session_id})

        last_messages: list[BaseMessage] = []
        try:
            async for chunk in app.state.agent.astream(
                {"messages": [("user", req.feature_description)]},
                config={**config, "recursion_limit": 20},
                stream_mode="values",
            ):
                msgs: list[BaseMessage] = chunk.get("messages") or []
                last_messages = msgs
                if not msgs:
                    continue
                latest = msgs[-1]

                if isinstance(latest, AIMessage):
                    if latest.tool_calls:
                        for call in latest.tool_calls:
                            tool_calls += 1
                            yield _sse("tool_call", {
                                "name": call.get("name"),
                                "args": call.get("args"),
                            })
                    elif latest.content:
                        # Mid-loop AI text is unusual but stream it anyway.
                        yield _sse("ai_message", {
                            "content": latest.content if isinstance(latest.content, str) else str(latest.content)
                        })

                elif isinstance(latest, ToolMessage):
                    content = latest.content if isinstance(latest.content, str) else str(latest.content)
                    yield _sse("tool_result", {
                        "name":    latest.name,
                        "preview": content[:400],
                    })

                # Small yield to flush the buffer to the client.
                await asyncio.sleep(0)

        except Exception as exc:
            yield _sse("error", {"detail": str(exc)})
            return

        extracted = extract_final_report_or_raw(last_messages)
        yield _sse("final", {
            "session_id": session_id,
            "source":     extracted.get("source") or "unknown",
            "report":     extracted.get("report"),
            "raw":        extracted.get("raw"),
            "error":      extracted.get("error"),
            "latency_s":  round(time.perf_counter() - started, 3),
            "tool_calls": tool_calls,
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",       # disable nginx buffering if proxied
    })


def _sse(event: str, data: dict) -> bytes:
    """Format a single Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


# ── Backend-compat endpoint ────────────────────────────────────────────────
#
# The `backend` service (FastAPI :8000) calls `POST /analyze` with a payload
# of {project_id, project_name, project_description, feature_text} and
# expects a response of {dashboard: {zones, risks, checklist, documents},
# summary}. That contract pre-dates the structured FinalReport we now
# produce — this endpoint adapts between the two so nothing on the backend
# side has to change.

class _BackendAnalyzeRequest(BaseModel):
    project_id:          Optional[str] = None
    project_name:        Optional[str] = None
    project_description: Optional[str] = None
    feature_text:        str = Field(..., min_length=1)


@app.post("/analyze")
async def analyze_legacy(req: _BackendAnalyzeRequest) -> dict:
    """Legacy contract for backend/app/routers/analysis.py — accepts
    `feature_text`, returns `{dashboard, summary}` shape."""
    session_id = req.project_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": session_id}}

    project_prefix = ""
    if req.project_name or req.project_description:
        project_prefix = (
            f"Контекст продукта: {req.project_name or ''}. "
            f"{req.project_description or ''}\n\n"
        )

    full_input = f"{project_prefix}Фича: {req.feature_text}"

    try:
        result = await app.state.agent.ainvoke(
            {"messages": [("user", full_input)]},
            config={**config, "recursion_limit": 20},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    messages = result.get("messages") or []
    extracted = extract_final_report_or_raw(messages)
    report = extracted.get("report") or {}

    # Server-side enrichment: every doc_id referenced in the report gets
    # its real source_url + title pulled from Elasticsearch and stitched
    # into each checklist item under `references: [{doc_id, title, source_url}]`.
    # Frontend renders those as clickable [title](url) markdown links.
    await _enrich_doc_links(report)

    return {
        "dashboard": _final_report_to_dashboard(report),
        "summary":   report.get("feature_summary") or req.feature_text[:200],
        "report":    report,        # full structured FinalReport for clients that want it
    }


async def _enrich_doc_links(report: dict) -> None:
    """Mutate `report` in-place: for every doc_id mentioned in the
    checklist, attach a `references` list with [{doc_id, title, source_url, source}].

    Does one batched mget against ES regardless of how many doc_ids
    appear (typically 1-5), so this adds ~10-30ms total.
    """
    from .tools._es import get_es

    domains = report.get("domains") or []
    all_ids: set[str] = set()
    for d in domains:
        for item in d.get("checklist") or []:
            for did in item.get("doc_links") or []:
                if isinstance(did, str) and did:
                    all_ids.add(did)
    if not all_ids:
        return

    try:
        resp = await get_es().mget(
            index="regtech-docs",
            body={"ids": sorted(all_ids)},
            source=["doc_id", "title", "source", "source_url"],
        )
    except Exception:
        return                          # silently skip if ES hiccups

    lookup: dict[str, dict] = {}
    for doc in resp.get("docs") or []:
        if doc.get("found") and doc.get("_source"):
            src = doc["_source"]
            lookup[src.get("doc_id") or doc.get("_id")] = {
                "doc_id":     src.get("doc_id") or doc.get("_id"),
                "title":      src.get("title") or doc.get("_id"),
                "source":     src.get("source"),
                "source_url": src.get("source_url"),
            }

    for d in domains:
        for item in d.get("checklist") or []:
            refs = []
            for did in item.get("doc_links") or []:
                ref = lookup.get(did)
                if ref:
                    refs.append(ref)
                else:
                    # doc_id not in index — surface so frontend can show "missing"
                    refs.append({"doc_id": did, "title": did, "source": None, "source_url": None})
            item["references"] = refs


def _final_report_to_dashboard(report: dict) -> dict:
    """Adapt the FinalReport (domains/checklist/...) into the legacy
    dashboard shape (zones/risks/checklist/documents) the frontend
    already renders."""
    domains = report.get("domains") or []
    zones, risks = [], []
    by_role: dict[str, list[str]] = {}

    for d in domains:
        domain_id = d.get("domain") or "other"
        severity  = d.get("risk_level") or "medium"

        zones.append({
            "id":       domain_id,
            "label":    _humanize_tag(domain_id),
            "severity": severity,
        })

        # One "risk" per domain — the first checklist item carries the
        # canonical doc_links + quotes that we surface as proof.
        first_item = (d.get("checklist") or [{}])[0]
        doc_links  = first_item.get("doc_links") or []
        quotes     = first_item.get("quotes") or []
        risks.append({
            "zone_id":     domain_id,
            "explanation": d.get("risk_assessment_details") or d.get("reasoning") or "",
            "article":     ", ".join(doc_links[:2]),
            "url":         f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{doc_links[0]}" if doc_links and doc_links[0].startswith("eurlex-") else (doc_links[0] if doc_links else ""),
            "severity":    severity,
            "quote":       quotes[0] if quotes else "",
        })

        for item in d.get("checklist") or []:
            role = (item.get("role") or "Engineering").strip()
            by_role.setdefault(role, []).append(item.get("action") or "")

    checklist = [
        {"role": role, "items": [i for i in items if i]}
        for role, items in by_role.items()
    ]

    return {
        "zones":     zones,
        "risks":     risks,
        "checklist": checklist,
        "documents": report.get("documents_to_update") or [],
    }


_TAG_LABEL = {
    "personal_data":        "Персональные данные / GDPR",
    "aml_cft":              "AML / CFT",
    "kyc":                  "KYC",
    "payments":             "Платежи / PSD2",
    "ai_scoring":           "AI / Скоринг",
    "cybersecurity":        "Кибербезопасность",
    "consumer_protection":  "Защита прав потребителей",
    "crypto":               "Криптоактивы",
    "reporting":            "Отчётность",
    "data_protection":      "Защита данных",
}

def _humanize_tag(tag: str) -> str:
    return _TAG_LABEL.get(tag, tag.replace("_", " ").title())
