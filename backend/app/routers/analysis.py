import json
import asyncio
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Project, Analysis, AnalysisFile
from app.schemas.analysis import AnalysisOut, AnalysisHistoryResponse, FileInfo
from app import s3

router = APIRouter(prefix="/projects", tags=["Analysis"])

ALLOWED_MIME_TYPES = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 МБ
MAX_FILES = 5

# Stub AI response — заменится реальным вызовом AI-сервиса
_STUB_RESULT = {
    "zones": [
        {"id": "gdpr", "label": "GDPR", "severity": "high"},
        {"id": "psd2", "label": "PSD2/PSD3", "severity": "medium"},
    ],
    "risks": [
        {
            "zone_id": "gdpr",
            "explanation": "Обработка платёжных данных подпадает под Art. 9 GDPR.",
            "article": "GDPR Art. 9",
            "url": "https://gdpr-info.eu/art-9-gdpr/",
            "severity": "high",
        }
    ],
    "checklist": [
        {"role": "PO", "items": ["Добавить экран согласия на обработку данных"]},
        {"role": "Compliance", "items": ["Провести DPIA (Art. 35 GDPR)"]},
        {"role": "Engineering", "items": ["Логировать операции с timestamp"]},
    ],
    "documents": [
        "Privacy Policy → раздел 'Платёжные данные'",
        "Terms of Service → раздел 'Виртуальные карты'",
    ],
}


def _get_project_or_404(project_id: str, user_id: str, db: Session) -> Project:
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.user_id == user_id,
    ).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": "Проект не найден"})
    return project


def _file_infos(analysis: Analysis) -> List[FileInfo]:
    result = []
    for f in analysis.files:
        try:
            url = s3.get_presigned_url(f.s3_key)
        except Exception:
            url = None
        result.append(FileInfo(name=f.filename, size=f.size, url=url))
    return result


def _analysis_out(a: Analysis) -> AnalysisOut:
    result = json.loads(a.result_json) if a.result_json else {}
    return AnalysisOut(
        id=a.id,
        project_id=a.project_id,
        text=a.text,
        files=_file_infos(a),
        zones=result.get("zones", []),
        risks=result.get("risks", []),
        checklist=result.get("checklist", []),
        documents=result.get("documents", []),
        created_at=a.created_at.isoformat(),
    )


@router.post("/{project_id}/analyze",
             summary="Запустить анализ фичи (SSE-поток)",
             responses={200: {"content": {"text/event-stream": {}}}})
async def analyze(
    project_id: str,
    text: Optional[str] = Form(None, description="User story или свободный текст"),
    files: Optional[List[UploadFile]] = File(None, description="PDF или DOCX, до 5 файлов по 10 МБ"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not text and not files:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"code": "EMPTY_INPUT", "message": "Нет ни текста, ни файлов"})

    _get_project_or_404(project_id, current_user.id, db)

    # Валидация файлов
    if files:
        if len(files) > MAX_FILES:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail={"code": "TOO_MANY_FILES", "message": f"Максимум {MAX_FILES} файлов"})
        for f in files:
            if f.content_type not in ALLOWED_MIME_TYPES:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail={"code": "UNSUPPORTED_FORMAT", "message": f"{f.filename}: только PDF и DOCX"})

    # Создаём запись анализа
    analysis = Analysis(
        project_id=project_id,
        text=text or "",
        result_json=json.dumps(_STUB_RESULT, ensure_ascii=False),
    )
    db.add(analysis)
    db.flush()  # получаем analysis.id до commit

    # Загружаем файлы в S3
    uploaded: List[AnalysisFile] = []
    if files:
        for upload in files:
            file_bytes = await upload.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                db.rollback()
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail={"code": "FILE_TOO_LARGE", "message": f"{upload.filename}: превышает 10 МБ"})
            try:
                key, size = s3.upload_file(file_bytes, analysis.id, upload.filename)
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail={"code": "S3_UNAVAILABLE", "message": f"Не удалось загрузить файл: {e}"})

            af = AnalysisFile(
                analysis_id=analysis.id,
                filename=upload.filename,
                s3_key=key,
                size=size,
                mime_type=upload.content_type,
            )
            db.add(af)
            uploaded.append(af)

    db.commit()
    db.refresh(analysis)

    analysis_id = analysis.id

    async def sse_stream():
        events = [
            ("zones",     {"zones": _STUB_RESULT["zones"]}),
            ("risks",     {"risks": _STUB_RESULT["risks"]}),
            ("checklist", {"checklist": _STUB_RESULT["checklist"]}),
            ("documents", {"documents": _STUB_RESULT["documents"]}),
            ("done",      {"analysis_id": analysis_id}),
        ]
        for event_name, data in events:
            await asyncio.sleep(0.4)
            yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


@router.get("/{project_id}/history", response_model=AnalysisHistoryResponse,
            summary="История анализов проекта")
def get_history(
    project_id: str,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_project_or_404(project_id, current_user.id, db)

    total = db.query(Analysis).filter(Analysis.project_id == project_id).count()
    analyses = (
        db.query(Analysis)
        .filter(Analysis.project_id == project_id)
        .order_by(Analysis.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for a in analyses:
        result = json.loads(a.result_json) if a.result_json else {}
        items.append({
            "id": a.id,
            "text_preview": a.text[:120] + ("..." if len(a.text) > 120 else ""),
            "files": _file_infos(a),
            "zones": result.get("zones", []),
            "created_at": a.created_at.isoformat(),
        })

    return {"items": items, "total": total}


@router.get("/{project_id}/history/{analysis_id}", response_model=AnalysisOut,
            summary="Полный результат конкретного анализа")
def get_analysis(
    project_id: str,
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_project_or_404(project_id, current_user.id, db)

    analysis = db.query(Analysis).filter(
        Analysis.id == analysis_id,
        Analysis.project_id == project_id,
    ).first()
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"code": "NOT_FOUND", "message": "Анализ не найден"})

    return _analysis_out(analysis)
