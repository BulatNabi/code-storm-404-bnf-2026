from typing import Optional, List
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from app.schemas.analysis import AnalysisOut, AnalysisHistoryResponse
import json
import asyncio

router = APIRouter(prefix="/projects", tags=["Analysis"])

_STUB_ANALYSIS = {
    "id": "00000000-0000-0000-0000-000000000020",
    "project_id": "00000000-0000-0000-0000-000000000010",
    "text": "Как клиент банка, я хочу создавать виртуальную карту для одной покупки...",
    "files": [{"name": "spec.pdf", "size": 204800}],
    "zones": [
        {"id": "gdpr", "label": "GDPR", "severity": "high"},
        {"id": "psd2", "label": "PSD2/PSD3", "severity": "medium"}
    ],
    "risks": [
        {
            "zone_id": "gdpr",
            "explanation": "Виртуальная карта привязана к профилю — обработка платёжных данных подпадает под Art. 9 GDPR.",
            "article": "GDPR Art. 9",
            "url": "https://gdpr-info.eu/art-9-gdpr/",
            "severity": "high"
        }
    ],
    "checklist": [
        {"role": "PO", "items": ["Добавить экран согласия на обработку данных карты"]},
        {"role": "Compliance", "items": ["Провести DPIA (Art. 35 GDPR)"]},
        {"role": "Engineering", "items": ["Логировать создание/удаление карты с timestamp"]}
    ],
    "documents": [
        "Privacy Policy → раздел 'Платёжные данные'",
        "Terms of Service → раздел 'Виртуальные карты'"
    ],
    "created_at": "2026-05-11T14:23:00Z"
}


async def _stub_sse_stream():
    events = [
        ("zones", {"zones": _STUB_ANALYSIS["zones"]}),
        ("risks", {"risks": _STUB_ANALYSIS["risks"]}),
        ("checklist", {"checklist": _STUB_ANALYSIS["checklist"]}),
        ("documents", {"documents": _STUB_ANALYSIS["documents"]}),
        ("done", {"analysis_id": _STUB_ANALYSIS["id"]}),
    ]
    for event_name, data in events:
        await asyncio.sleep(0.5)
        yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{project_id}/analyze",
             summary="Запустить анализ фичи (SSE-поток)",
             response_description="Server-Sent Events: zones → risks → checklist → documents → done",
             responses={200: {"content": {"text/event-stream": {}}}})
async def analyze(
    project_id: str,
    text: Optional[str] = Form(None, description="User story или свободный текст"),
    files: Optional[List[UploadFile]] = File(None, description="PDF или DOCX, до 5 файлов по 10 МБ")
):
    return StreamingResponse(_stub_sse_stream(), media_type="text/event-stream")


@router.get("/{project_id}/history", response_model=AnalysisHistoryResponse,
            summary="История анализов проекта")
async def get_history(project_id: str, limit: int = 20, offset: int = 0):
    item = {
        "id": _STUB_ANALYSIS["id"],
        "text_preview": _STUB_ANALYSIS["text"][:80] + "...",
        "files": _STUB_ANALYSIS["files"],
        "zones": _STUB_ANALYSIS["zones"],
        "created_at": _STUB_ANALYSIS["created_at"]
    }
    return {"items": [item], "total": 1}


@router.get("/{project_id}/history/{analysis_id}", response_model=AnalysisOut,
            summary="Полный результат конкретного анализа")
async def get_analysis(project_id: str, analysis_id: str):
    return _STUB_ANALYSIS
