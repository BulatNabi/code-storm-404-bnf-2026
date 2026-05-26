import json
import logging
import os
from typing import Optional, List

logger = logging.getLogger(__name__)

import httpx
from fastapi import APIRouter, Depends, HTTPException, Form, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Project, Analysis, JiraBoard
from app.schemas.analysis import AnalyzeResponse, AnalysisOut, AnalysisHistoryResponse, Dashboard
from app.services.jira_client import JiraAPIClient

router = APIRouter(prefix="/projects", tags=["Analysis"])

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://fintech-radar-ai:8001")

# Когда AI-сервис недоступен — отдаём пустой дашборд и понятное сообщение
# (фронт рендерит summary как текст бабла, если он есть).
_EMPTY_DASHBOARD = {"zones": [], "risks": [], "checklist": [], "documents": []}

_AI_UNAVAILABLE_MESSAGE = (
    "⚠️ **AI-сервис сейчас недоступен** — выполнить анализ не получилось.\n\n"
    "Что можно сделать:\n"
    "- Повторить запрос через минуту\n"
    "- Проверить, что AI-сервис (ai-core) запущен и доступен\n"
    "- Если ошибка повторяется — сообщить администратору\n\n"
    "Запрос сохранён в истории, повторная отправка ничего не сломает."
)


def _build_jira_comment(summary: dict) -> str:
    lines = [f"🔍 {summary.get('title', 'Compliance Review')}", "", summary.get('description', ''), ""]
    checklist = summary.get("checklist", [])
    if checklist:
        lines.append("Чеклист:")
        lines.extend(f"• {item}" for item in checklist)
    return "\n".join(lines)


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
        # AI-сервис недоступен — отдаём пустой дашборд + понятное сообщение
        return {"dashboard": _EMPTY_DASHBOARD, "summary": _AI_UNAVAILABLE_MESSAGE}


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
    dashboard = ai_response.get("dashboard", _EMPTY_DASHBOARD)
    summary = ai_response.get("summary")
    report = ai_response.get("report")     # full FinalReport — pass-through

    result_json = json.dumps(
        {"dashboard": dashboard, "summary": summary, "report": report},
        ensure_ascii=False,
    )

    analysis = Analysis(
        project_id=project_id,
        text=text,
        jira_issue_key=jira_issue_key,
        result_json=result_json,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    if jira_issue_key and project.jira_board_id and summary:
        board = db.query(JiraBoard).filter(
            JiraBoard.board_key == project.jira_board_id,
            JiraBoard.user_id == current_user.id,
        ).first()
        if board:
            try:
                client = JiraAPIClient(board.domain, board.email, board.api_token)
                comment = _build_jira_comment(summary)
                await client.post_comment(jira_issue_key, comment)
            except Exception as e:
                logger.warning("Failed to post Jira comment: %s", e)

    return AnalyzeResponse(
        analysis_id=analysis.id,
        dashboard=dashboard,
        report=report,
        summary=summary,
    )


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
        report=result.get("report"),
        summary=result.get("summary"),
        created_at=analysis.created_at.isoformat(),
    )
