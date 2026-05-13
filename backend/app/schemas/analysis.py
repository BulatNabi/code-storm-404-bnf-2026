from typing import Optional, List, Any, Dict
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


class Dashboard(BaseModel):
    zones: List[Zone]
    risks: List[Risk]
    checklist: List[ChecklistGroup]
    documents: List[str]


class AnalyzeResponse(BaseModel):
    analysis_id: str
    dashboard: Dashboard
    # Full FinalReport from ai-core (with domains, checklist items,
    # rationales, compliance metrics, red flags, jira summary). Free-form
    # dict to avoid breaking when ai-core schema evolves.
    report: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None


class AnalysisHistoryItem(BaseModel):
    id: str
    text_preview: str
    jira_issue_key: Optional[str]
    zones: List[Zone]
    created_at: str


class AnalysisHistoryResponse(BaseModel):
    items: List[AnalysisHistoryItem]
    total: int


class AnalysisOut(BaseModel):
    id: str
    project_id: str
    text: str
    jira_issue_key: Optional[str]
    dashboard: Dashboard
    report: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    created_at: str
