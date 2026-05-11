from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Project
from app.schemas.projects import (
    ProjectCreate, ProjectUpdate, ProjectOut, ProjectListResponse
)

router = APIRouter(prefix="/projects", tags=["Projects"])


def _project_out(p: Project, analysis_count: int = 0, last_analysis_at=None) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description,
        analysis_count=analysis_count,
        last_analysis_at=last_analysis_at.isoformat() if last_analysis_at else None,
        created_at=p.created_at.isoformat(),
    )


@router.get("", response_model=ProjectListResponse,
            summary="Список проектов текущего пользователя")
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    projects = (
        db.query(Project)
        .filter(Project.user_id == current_user.id)
        .order_by(Project.updated_at.desc())
        .all()
    )
    items = []
    for p in projects:
        count = len(p.analyses)
        last = max((a.created_at for a in p.analyses), default=None)
        items.append(_project_out(p, count, last))
    return {"items": items, "total": len(items)}


@router.post("", response_model=ProjectOut, status_code=201,
             summary="Создать новый проект")
def create_project(
    body: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = Project(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_out(project)


@router.get("/{project_id}", response_model=ProjectOut,
            summary="Получить проект по ID")
def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id,
    ).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": "Проект не найден"})
    count = len(project.analyses)
    last = max((a.created_at for a in project.analyses), default=None)
    return _project_out(project, count, last)


@router.patch("/{project_id}", response_model=ProjectOut,
              summary="Обновить название или описание проекта")
def update_project(
    project_id: str,
    body: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id,
    ).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": "Проект не найден"})
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    db.commit()
    db.refresh(project)
    return _project_out(project, len(project.analyses))


@router.delete("/{project_id}", status_code=204,
               summary="Удалить проект и всю историю анализов")
def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == current_user.id,
    ).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": "Проект не найден"})
    db.delete(project)
    db.commit()
    return None
