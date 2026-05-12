from pydantic import BaseModel, Field
from typing import Optional

class SyncStatusResponse(BaseModel):
    status: str = Field(..., example="ready", description="Статус: 'ready' или 'error'")
    source: str = Field(..., example="jira", description="Источник: 'jira' или 'cache'")
    path: str = Field(..., example="data/jira/COD-1", description="Путь к папке для ML")
    message: Optional[str] = Field(None, example="Files downloaded.", description="Сообщение")