from fastapi import APIRouter
from app.schemas.jira import (
    JiraConnectRequest, JiraConnection, JiraIssueListResponse,
    JiraIssueDetail, JiraImportRequest
)

router = APIRouter(prefix="/integrations/jira", tags=["Jira Integration"])

_STUB_CONNECTION = JiraConnection(
    connected=True,
    domain="mycompany.atlassian.net",
    email="user@mycompany.com",
    connected_at="2026-05-11T10:00:00Z"
)

_STUB_ISSUES = [
    {
        "key": "BANK-42",
        "summary": "Виртуальная карта для одной покупки",
        "status": "In Progress",
        "issue_type": "Story",
        "assignee": "Ivan Petrov",
        "updated_at": "2026-05-10T18:00:00Z",
        "has_attachments": True
    },
    {
        "key": "BANK-38",
        "summary": "Push-уведомления о подозрительных транзакциях",
        "status": "To Do",
        "issue_type": "Story",
        "assignee": None,
        "updated_at": "2026-05-09T12:00:00Z",
        "has_attachments": False
    }
]


@router.get("", response_model=JiraConnection,
            summary="Статус подключения Jira для текущего пользователя")
async def get_jira_status():
    return _STUB_CONNECTION


@router.post("/connect", response_model=JiraConnection,
             summary="Привязать Jira-аккаунт (domain + email + API token)")
async def connect_jira(body: JiraConnectRequest):
    return JiraConnection(
        connected=True,
        domain=body.domain,
        email=body.email,
        connected_at="2026-05-11T15:00:00Z"
    )


@router.delete("", status_code=204,
               summary="Отвязать Jira-аккаунт")
async def disconnect_jira():
    return None


@router.get("/issues", response_model=JiraIssueListResponse,
            summary="Список задач из Jira пользователя")
async def list_jira_issues(q: str = ""):
    items = _STUB_ISSUES
    if q:
        items = [i for i in items if q.lower() in i["summary"].lower()]
    return {"items": items, "total": len(items)}


@router.get("/issues/{issue_key}", response_model=JiraIssueDetail,
            summary="Детали таски: описание и список вложений")
async def get_jira_issue(issue_key: str):
    return JiraIssueDetail(
        key=issue_key,
        summary="Виртуальная карта для одной покупки",
        description="Как клиент банка, я хочу создавать виртуальную карту для одной покупки, чтобы безопасно оплачивать товары на новых сайтах.",
        status="In Progress",
        issue_type="Story",
        attachments=[
            {
                "id": "att-001",
                "filename": "spec_virtual_card.pdf",
                "size": 204800,
                "mime_type": "application/pdf",
                "url": f"https://mycompany.atlassian.net/rest/api/3/attachment/content/att-001"
            }
        ]
    )


@router.post("/issues/{issue_key}/import",
             summary="Скачать вложения таски и вернуть для отправки в /analyze")
async def import_jira_attachments(issue_key: str, body: JiraImportRequest):
    # stub: в реальности бек проксирует файлы из Jira и возвращает их фронту
    return {
        "issue_key": issue_key,
        "imported": [
            {"attachment_id": aid, "filename": f"attachment_{aid}.pdf", "size": 204800}
            for aid in body.attachment_ids
        ]
    }
