from typing import Optional, List
from pydantic import BaseModel


# ── Подключение аккаунта ─────────────────────────────────────────────────────

class JiraConnectRequest(BaseModel):
    domain: str
    email: str
    api_token: str

    model_config = {"json_schema_extra": {"example": {
        "domain": "mycompany.atlassian.net",
        "email": "user@mycompany.com",
        "api_token": "ATATT3x..."
    }}}


class JiraConnection(BaseModel):
    connected: bool
    domain: Optional[str] = None
    email: Optional[str] = None
    connected_at: Optional[str] = None


# ── Доски (проекты Jira) ─────────────────────────────────────────────────────

class JiraBoard(BaseModel):
    board_key: str               # ключ проекта в Jira, напр. "BANK"
    board_name: str
    project_type: Optional[str] = None


class JiraBoardListResponse(BaseModel):
    items: List[JiraBoard]
    total: int


# ── Задачи ───────────────────────────────────────────────────────────────────

class JiraIssue(BaseModel):
    key: str
    summary: str
    status: str
    issue_type: str
    assignee: Optional[str] = None
    updated_at: Optional[str] = None
    has_attachments: bool


class JiraIssueListResponse(BaseModel):
    items: List[JiraIssue]
    total: int


class JiraAttachment(BaseModel):
    id: str
    filename: str
    size: int
    mime_type: str
    url: str


class JiraIssueDetail(BaseModel):
    key: str
    summary: str
    description: str
    status: str
    issue_type: str
    attachments: List[JiraAttachment]


# ── Импорт вложений ──────────────────────────────────────────────────────────

class JiraImportRequest(BaseModel):
    attachment_ids: List[str]

    model_config = {"json_schema_extra": {"example": {
        "attachment_ids": ["10001", "10002"]
    }}}


class JiraImportedAttachment(BaseModel):
    attachment_id: str
    filename: str
    size: int
    mime_type: str
    content_base64: str


class JiraImportResponse(BaseModel):
    issue_key: str
    imported: List[JiraImportedAttachment]
