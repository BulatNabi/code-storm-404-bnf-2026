from typing import Optional, List
from pydantic import BaseModel


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


class JiraIssue(BaseModel):
    key: str
    summary: str
    status: str
    issue_type: str
    assignee: Optional[str]
    updated_at: str
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


class JiraImportRequest(BaseModel):
    attachment_ids: List[str]

    model_config = {"json_schema_extra": {"example": {
        "attachment_ids": ["att-001", "att-002"]
    }}}