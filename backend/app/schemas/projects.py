from typing import Optional, List
from pydantic import BaseModel


class FileInfo(BaseModel):
    name: str
    size: int
    url: Optional[str] = None  # presigned S3 URL


class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    jira_board_id: Optional[str]
    files: List[FileInfo]
    analysis_count: int
    last_analysis_at: Optional[str]
    created_at: str


class ProjectListResponse(BaseModel):
    items: List[ProjectOut]
    total: int


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
