from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Project, ProjectFile
from app.schemas.projects import ProjectOut, ProjectUpdate, ProjectListResponse, FileInfo
from app import s3, kafka_producer

router = APIRouter(prefix="/projects", tags=["Projects"])

ALLOWED_MIME_TYPES = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 МБ
MAX_FILES = 5


def _file_infos(project: Project) -> List[FileInfo]:
    result = []
    for f in project.files:
        try:
            url = s3.get_presigned_url(f.s3_key)
        except Exception:
            url = None
        result.append(FileInfo(name=f.filename, size=f.size, url=url))
    return result


def _project_out(p: Project, analysis_count: int = 0, last_analysis_at=None) -> ProjectOut:
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description,
        jira_board_id=p.jira_board_id,
        files=_file_infos(p),
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
             summary="Создать проект (multipart: name, description, jira_board_id, files)")
async def create_project(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    jira_board_id: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None, description="PDF или DOCX, до 5 файлов по 10 МБ"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if files:
        if len(files) > MAX_FILES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail={"code": "TOO_MANY_FILES", "message": f"Максимум {MAX_FILES} файлов"})
        for f in files:
            if f.content_type not in ALLOWED_MIME_TYPES:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail={"code": "UNSUPPORTED_FORMAT", "message": f"{f.filename}: только PDF и DOCX"})

    project = Project(
        user_id=current_user.id,
        name=name,
        description=description,
        jira_board_id=jira_board_id,
    )
    db.add(project)
    db.flush()  # получаем project.id до commit

    if files:
        for upload in files:
            file_bytes = await upload.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                db.rollback()
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail={"code": "FILE_TOO_LARGE", "message": f"{upload.filename}: превышает 10 МБ"})
            try:
                key, size = s3.upload_file(file_bytes, project.id, upload.filename)
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail={"code": "S3_UNAVAILABLE", "message": f"Не удалось загрузить файл: {e}"})

            db.add(ProjectFile(
                project_id=project.id,
                filename=upload.filename,
                s3_key=key,
                size=size,
                mime_type=upload.content_type,
            ))

    db.commit()
    db.refresh(project)

    if project.files:
        await kafka_producer.publish_file_vectorization(
            project_id=project.id,
            file_ids=[f.id for f in project.files],
            s3_keys=[f.s3_key for f in project.files],
        )

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
