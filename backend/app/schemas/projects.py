from typing import Optional, List
from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None

    model_config = {"json_schema_extra": {"example": {
        "name": "Мобильный банк v2",
        "description": "Фичи Q3 2026"
    }}}


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    analysis_count: int
    last_analysis_at: Optional[str]
    created_at: str


class ProjectListResponse(BaseModel):
    items: List[ProjectOut]
    total: int
