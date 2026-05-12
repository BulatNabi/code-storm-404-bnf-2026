from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProjectFile

router = APIRouter(prefix="/internal", tags=["Internal (AI worker)"])


class VectorizedRequest(BaseModel):
    file_ids: List[str]


class VectorizedResponse(BaseModel):
    updated: int  # сколько файлов помечено


@router.patch(
    "/projects/{project_id}/files/vectorized",
    response_model=VectorizedResponse,
    summary="Пометить файлы проекта как векторизованные (вызывается AI-воркером)",
)
def mark_files_vectorized(
    project_id: str,
    body: VectorizedRequest,
    db: Session = Depends(get_db),
):
    if not body.file_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EMPTY_LIST", "message": "file_ids не может быть пустым"},
        )

    rows = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.project_id == project_id,
            ProjectFile.id.in_(body.file_ids),
        )
        .all()
    )

    for f in rows:
        f.vectorized = True

    db.commit()
    return VectorizedResponse(updated=len(rows))
