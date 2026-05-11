from fastapi import APIRouter, Depends
from app.dependencies import get_current_user
from app.models import User
from app.schemas.projects import (
    ProjectCreate, ProjectUpdate, ProjectOut, ProjectListResponse
)

router = APIRouter(prefix="/projects", tags=["Projects"])

_STUB_PROJECT: ProjectOut = ProjectOut(
    id="00000000-0000-0000-0000-000000000010",
    name="Мобильный банк v2",
    description="Фичи Q3 2026",
    analysis_count=3,
    last_analysis_at="2026-05-11T14:23:00Z",
    created_at="2026-05-01T10:00:00Z"
)


@router.get("", response_model=ProjectListResponse,
            summary="Список проектов текущего пользователя")
async def list_projects(current_user: User = Depends(get_current_user)):
    return {"items": [_STUB_PROJECT], "total": 1}


@router.post("", response_model=ProjectOut, status_code=201,
             summary="Создать новый проект")
async def create_project(body: ProjectCreate, current_user: User = Depends(get_current_user)):
    return ProjectOut(
        id="00000000-0000-0000-0000-000000000011",
        name=body.name,
        description=body.description,
        analysis_count=0,
        last_analysis_at=None,
        created_at="2026-05-11T15:00:00Z"
    )


@router.get("/{project_id}", response_model=ProjectOut,
            summary="Получить проект по ID")
async def get_project(project_id: str, current_user: User = Depends(get_current_user)):
    return _STUB_PROJECT


@router.patch("/{project_id}", response_model=ProjectOut,
              summary="Обновить название или описание проекта")
async def update_project(project_id: str, body: ProjectUpdate, current_user: User = Depends(get_current_user)):
    return _STUB_PROJECT


@router.delete("/{project_id}", status_code=204,
               summary="Удалить проект и всю историю анализов")
async def delete_project(project_id: str, current_user: User = Depends(get_current_user)):
    return None
