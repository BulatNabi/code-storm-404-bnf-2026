import base64
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, JiraConnection
from app.schemas.jira import (
    JiraConnectRequest,
    JiraConnection as JiraConnectionOut,
    JiraBoard, JiraBoardListResponse,
    JiraIssueListResponse, JiraIssueDetail, JiraIssue, JiraAttachment,
    JiraImportRequest, JiraImportResponse, JiraImportedAttachment,
)
from app.services.jira_client import JiraAPIClient

router = APIRouter(prefix="/integrations/jira", tags=["Jira Integration"])
logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_connection(user_id: str, db: Session) -> JiraConnection | None:
    return db.query(JiraConnection).filter(JiraConnection.user_id == user_id).first()


def _require_connection(user_id: str, db: Session) -> JiraConnection:
    conn = _get_connection(user_id, db)
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "NOT_CONNECTED", "message": "Jira не подключена"},
        )
    return conn


def _client(conn: JiraConnection) -> JiraAPIClient:
    return JiraAPIClient(conn.domain, conn.email, conn.api_token)


def _adf_to_text(adf: dict) -> str:
    """Грубая экстракция текста из Atlassian Document Format."""
    return " ".join(
        block.get("content", [{}])[0].get("text", "")
        for block in adf.get("content", [])
    )


# ── подключение аккаунта ─────────────────────────────────────────────────────

@router.get("", response_model=JiraConnectionOut,
            summary="Статус подключения Jira для текущего пользователя")
def get_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = _get_connection(current_user.id, db)
    if not conn:
        return JiraConnectionOut(connected=False)
    return JiraConnectionOut(
        connected=True,
        domain=conn.domain,
        email=conn.email,
        connected_at=conn.connected_at.isoformat(),
    )


@router.post("/connect", response_model=JiraConnectionOut,
             summary="Привязать Jira-аккаунт (domain + email + API token)")
async def connect(
    body: JiraConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = JiraAPIClient(body.domain, body.email, body.api_token)
    try:
        ok = await client.verify_connection()
    except httpx.HTTPError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail={"code": "JIRA_ERROR", "message": "Ошибка при обращении к Jira"})
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"code": "INVALID_CREDENTIALS", "message": "Неверные Jira credentials"})

    # upsert — одно подключение на пользователя
    conn = _get_connection(current_user.id, db)
    if conn:
        conn.domain = body.domain
        conn.email = body.email
        conn.api_token = body.api_token
        conn.connected_at = datetime.now(timezone.utc)
    else:
        conn = JiraConnection(
            user_id=current_user.id,
            domain=body.domain,
            email=body.email,
            api_token=body.api_token,
        )
        db.add(conn)
    db.commit()
    db.refresh(conn)

    return JiraConnectionOut(
        connected=True,
        domain=conn.domain,
        email=conn.email,
        connected_at=conn.connected_at.isoformat(),
    )


@router.delete("", status_code=204,
               summary="Отвязать Jira-аккаунт")
def disconnect(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = _get_connection(current_user.id, db)
    if conn:
        db.delete(conn)
        db.commit()
    return None


# ── доски (проекты Jira) ─────────────────────────────────────────────────────

@router.get("/boards", response_model=JiraBoardListResponse,
            summary="Все доски (проекты) подключённого аккаунта Jira")
async def list_boards(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = _require_connection(current_user.id, db)
    client = _client(conn)
    try:
        raw = await client.list_projects()
    except Exception as e:
        logger.error("Jira API error: %s", e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail={"code": "JIRA_ERROR", "message": "Не удалось получить доски из Jira"})

    items = [
        JiraBoard(
            board_key=p.get("key"),
            board_name=p.get("name", ""),
            project_type=p.get("projectTypeKey"),
        )
        for p in raw
    ]
    return JiraBoardListResponse(items=items, total=len(items))


@router.get("/boards/{board_key}/issues", response_model=JiraIssueListResponse,
            summary="Список задач доски")
async def list_board_issues(
    board_key: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = _require_connection(current_user.id, db)
    client = _client(conn)
    try:
        raw = await client.search_project_issues(board_key)
    except Exception as e:
        logger.error("Jira API error: %s", e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail={"code": "JIRA_ERROR", "message": "Не удалось получить задачи из Jira"})

    items = []
    for issue in raw:
        fields = issue.get("fields", {})
        attachments = fields.get("attachment") or []
        assignee = fields.get("assignee")
        items.append(JiraIssue(
            key=issue.get("key"),
            summary=fields.get("summary", ""),
            status=fields.get("status", {}).get("name", ""),
            issue_type=fields.get("issuetype", {}).get("name", ""),
            assignee=assignee.get("displayName") if assignee else None,
            updated_at=fields.get("updated"),
            has_attachments=len(attachments) > 0,
        ))

    return JiraIssueListResponse(items=items, total=len(items))


@router.get("/boards/{board_key}/issues/{issue_key}", response_model=JiraIssueDetail,
            summary="Детали задачи (описание + вложения)")
async def get_issue(
    board_key: str,
    issue_key: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = _require_connection(current_user.id, db)
    client = _client(conn)
    try:
        raw = await client.get_issue_details(issue_key)
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": f"Задача {issue_key} не найдена"})

    fields = raw.get("fields", {})
    description = fields.get("description") or ""
    if isinstance(description, dict):
        description = _adf_to_text(description)

    attachments = [
        JiraAttachment(
            id=str(a.get("id")),
            filename=a.get("filename", ""),
            size=a.get("size", 0),
            mime_type=a.get("mimeType", ""),
            url=a.get("content", ""),
        )
        for a in (fields.get("attachment") or [])
    ]

    return JiraIssueDetail(
        key=raw.get("key"),
        summary=fields.get("summary", ""),
        description=description,
        status=fields.get("status", {}).get("name", ""),
        issue_type=fields.get("issuetype", {}).get("name", ""),
        attachments=attachments,
    )


@router.post("/boards/{board_key}/issues/{issue_key}/import", response_model=JiraImportResponse,
             summary="Скачать вложения задачи (base64) для отправки в /analyze")
async def import_attachments(
    board_key: str,
    issue_key: str,
    body: JiraImportRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = _require_connection(current_user.id, db)
    client = _client(conn)
    try:
        raw = await client.get_issue_details(issue_key)
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": f"Задача {issue_key} не найдена"})

    meta = {str(a.get("id")): a for a in (raw.get("fields", {}).get("attachment") or [])}
    imported = []
    for aid in body.attachment_ids:
        a = meta.get(aid)
        if not a:
            continue
        try:
            content = await client.download_attachment(aid)
        except Exception as e:
            logger.warning("Failed to download attachment %s: %s", aid, e)
            continue
        imported.append(JiraImportedAttachment(
            attachment_id=aid,
            filename=a.get("filename", ""),
            size=a.get("size", len(content)),
            mime_type=a.get("mimeType", ""),
            content_base64=base64.b64encode(content).decode("ascii"),
        ))

    return JiraImportResponse(issue_key=issue_key, imported=imported)
