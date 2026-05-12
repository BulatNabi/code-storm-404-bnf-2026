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
        result = await app.state.agent.ainvoke(
            {"messages": [("user", req.feature_description)]},
            config=config,
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
                config=config,
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
