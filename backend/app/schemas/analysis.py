from typing import Optional, List
from pydantic import BaseModel


class Zone(BaseModel):
    id: str
    label: str
    severity: str  # high | medium | low


class Risk(BaseModel):
    zone_id: str
    explanation: str
    article: str
    url: str
    severity: str


class ChecklistGroup(BaseModel):
    role: str  # PO | Compliance | Engineering
    items: List[str]


class FileInfo(BaseModel):
    name: str
    size: int


class AnalysisOut(BaseModel):
    id: str
    project_id: str
    text: str
    files: List[FileInfo]
    zones: List[Zone]
    risks: List[Risk]
    checklist: List[ChecklistGroup]
    documents: List[str]
    created_at: str


class AnalysisHistoryItem(BaseModel):
    id: str
    text_preview: str
    files: List[FileInfo]
    zones: List[Zone]
    created_at: str


class AnalysisHistoryResponse(BaseModel):
    items: List[AnalysisHistoryItem]
    total: int
