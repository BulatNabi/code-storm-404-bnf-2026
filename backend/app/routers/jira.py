import logging
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, JiraBoard
from app.schemas.jira import (
    JiraBoardRegisterRequest, JiraBoardOut,
    JiraIssueListResponse, JiraIssueDetail, JiraIssue, JiraAttachment,
)
from app.services.jira_client import JiraAPIClient

router = APIRouter(prefix="/integrations/jira", tags=["Jira Integration"])
logger = logging.getLogger(__name__)


def _get_client(board: JiraBoard) -> JiraAPIClient:
    return JiraAPIClient(board.domain, board.email, board.api_token)


def _get_board_or_404(board_key: str, user_id: str, db: Session) -> JiraBoard:
    board = db.query(JiraBoard).filter(
        JiraBoard.board_key == board_key,
        JiraBoard.user_id == user_id,
    ).first()
    if not board:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": f"Доска {board_key} не найдена"})
    return board


@router.get("/boards", response_model=List[JiraBoardOut],
            summary="Список зарегистрированных Jira-досок пользователя")
def list_boards(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    boards = db.query(JiraBoard).filter(JiraBoard.user_id == current_user.id).all()
    return [
        JiraBoardOut(
            id=b.id,
            board_key=b.board_key,
            board_name=b.board_name,
            domain=b.domain,
            email=b.email,
            created_at=b.created_at.isoformat(),
        )
        for b in boards
    ]


@router.post("/boards/register", response_model=JiraBoardOut, status_code=201,
             summary="Зарегистрировать Jira-доску (проверяет credentials и сохраняет)")
async def register_board(
    body: JiraBoardRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    client = JiraAPIClient(body.domain, body.email, body.api_token)
    try:
        ok = await client.verify_connection()
        if not ok:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail={"code": "INVALID_CREDENTIALS", "message": "Неверные Jira credentials"})
        # проверяем что проект существует
        await client._request("GET", f"/project/{body.board_key}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail={"code": "PROJECT_NOT_FOUND", "message": f"Проект {body.board_key} не найден в Jira"})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail={"code": "JIRA_ERROR", "message": "Ошибка при обращении к Jira"})

    # upsert — если доска с таким ключом уже есть у юзера, обновляем credentials
    existing = db.query(JiraBoard).filter(
        JiraBoard.board_key == body.board_key,
        JiraBoard.user_id == current_user.id,
    ).first()

    if existing:
        existing.board_name = body.board_name
        existing.domain = body.domain
        existing.email = body.email
        existing.api_token = body.api_token
        board = existing
    else:
        board = JiraBoard(
            user_id=current_user.id,
            board_key=body.board_key,
            board_name=body.board_name,
            domain=body.domain,
            email=body.email,
            api_token=body.api_token,
        )
        db.add(board)

    db.commit()
    db.refresh(board)

    return JiraBoardOut(
        id=board.id,
        board_key=board.board_key,
        board_name=board.board_name,
        domain=board.domain,
        email=board.email,
        created_at=board.created_at.isoformat(),
    )


@router.delete("/boards/{board_key}", status_code=204,
               summary="Удалить зарегистрированную доску")
def delete_board(
    board_key: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    board = _get_board_or_404(board_key, current_user.id, db)
    db.delete(board)
    db.commit()
    return None


@router.get("/boards/{board_key}/issues", response_model=JiraIssueListResponse,
            summary="Список задач доски")
async def list_board_issues(
    board_key: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    board = _get_board_or_404(board_key, current_user.id, db)
    client = _get_client(board)
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
    board = _get_board_or_404(board_key, current_user.id, db)
    client = _get_client(board)
    try:
        raw = await client.get_issue_details(issue_key)
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": f"Задача {issue_key} не найдена"})

    fields = raw.get("fields", {})
    description = fields.get("description") or ""
    if isinstance(description, dict):
        description = " ".join(
            block.get("content", [{}])[0].get("text", "")
            for block in description.get("content", [])
        )

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
