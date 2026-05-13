from typing import Optional, List
from pydantic import BaseModel


class JiraBoardRegisterRequest(BaseModel):
    board_key: str      # ключ проекта в Jira, напр. "BANK"
    board_name: str     # отображаемое имя
    domain: str         # mycompany.atlassian.net
    email: str
    api_token: str

    model_config = {"json_schema_extra": {"example": {
        "board_key": "BANK",
        "board_name": "Мобильный банк",
        "domain": "mycompany.atlassian.net",
        "email": "user@mycompany.com",
        "api_token": "ATATT3x..."
    }}}


class JiraBoardOut(BaseModel):
    id: str
    board_key: str
    board_name: str
    domain: str
    email: str
    created_at: str


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
