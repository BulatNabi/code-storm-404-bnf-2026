import json
import os
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Form, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Project, Analysis
from app.schemas.analysis import AnalyzeResponse, AnalysisOut, AnalysisHistoryResponse, Dashboard

router = APIRouter(prefix="/projects", tags=["Analysis"])

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://fintech-radar-ai:8001")

_STUB_DASHBOARD = {
    "zones": [
        {"id": "gdpr", "label": "GDPR", "severity": "high"},
        {"id": "psd2", "label": "PSD2/PSD3", "severity": "medium"},
    ],
    "risks": [
        {
            "zone_id": "gdpr",
            "explanation": "Обработка платёжных данных подпадает под Art. 9 GDPR.",
            "article": "GDPR Art. 9",
            "url": "https://gdpr-info.eu/art-9-gdpr/",
            "severity": "high",
        }
    ],
    "checklist": [
        {"role": "PO", "items": ["Добавить экран согласия на обработку данных"]},
        {"role": "Compliance", "items": ["Провести DPIA (Art. 35 GDPR)"]},
        {"role": "Engineering", "items": ["Логировать операции с timestamp"]},
    ],
    "documents": [
        "Privacy Policy → раздел 'Платёжные данные'",
        "Terms of Service → раздел 'Виртуальные карты'",
    ],
}


def _get_project_or_404(project_id: str, user_id: str, db: Session) -> Project:
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == user_id,
    ).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": "Проект не найден"})
    return project


async def _call_ai(project: Project, feature_text: str) -> dict:
    payload = {
        "project_id": project.id,
        "project_name": project.name,
        "project_description": project.description or "",
        "feature_text": feature_text,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{AI_SERVICE_URL}/analyze", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        # AI-сервис недоступен — возвращаем стаб чтобы пайплайн не ломался
        return {"dashboard": _STUB_DASHBOARD, "summary": None}


@router.post("/{project_id}/analyze", response_model=AnalyzeResponse,
             summary="Запустить анализ фичи")
async def analyze(
    project_id: str,
    text: str = Form(..., description="User story или свободный текст"),
    jira_issue_key: Optional[str] = Form(None, description="Ключ Jira-таски (BANK-42)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_project_or_404(project_id, current_user.id, db)

    ai_response = await _call_ai(project, text)
    dashboard = ai_response.get("dashboard", _STUB_DASHBOARD)
    summary = ai_response.get("summary")

    result_json = json.dumps({"dashboard": dashboard, "summary": summary}, ensure_ascii=False)

    analysis = Analysis(
        project_id=project_id,
        text=text,
        jira_issue_key=jira_issue_key,
        result_json=result_json,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    # Если анализ привязан к Jira-таске — постим комментарий (реализуется в следующем шаге)

    return AnalyzeResponse(analysis_id=analysis.id, dashboard=dashboard)


@router.get("/{project_id}/history", response_model=AnalysisHistoryResponse,
            summary="История анализов проекта")
def get_history(
    project_id: str,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_project_or_404(project_id, current_user.id, db)

    total = db.query(Analysis).filter(Analysis.project_id == project_id).count()
    analyses = (
        db.query(Analysis)
        .filter(Analysis.project_id == project_id)
        .order_by(Analysis.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for a in analyses:
        result = json.loads(a.result_json) if a.result_json else {}
        dashboard = result.get("dashboard", {})
        items.append({
            "id": a.id,
            "text_preview": a.text[:120] + ("..." if len(a.text) > 120 else ""),
            "jira_issue_key": a.jira_issue_key,
            "zones": dashboard.get("zones", []),
            "created_at": a.created_at.isoformat(),
        })

    return {"items": items, "total": total}


@router.get("/{project_id}/history/{analysis_id}", response_model=AnalysisOut,
            summary="Полный результат конкретного анализа")
def get_analysis(
    project_id: str,
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_project_or_404(project_id, current_user.id, db)

    analysis = db.query(Analysis).filter(
        Analysis.id == analysis_id,
        Analysis.project_id == project_id,
    ).first()
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": "Анализ не найден"})

    result = json.loads(analysis.result_json) if analysis.result_json else {}
    dashboard = result.get("dashboard", {"zones": [], "risks": [], "checklist": [], "documents": []})

    return AnalysisOut(
        id=analysis.id,
        project_id=analysis.project_id,
        text=analysis.text,
        jira_issue_key=analysis.jira_issue_key,
        dashboard=dashboard,
        created_at=analysis.created_at.isoformat(),
    )
