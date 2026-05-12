import logging
from fastapi import APIRouter, HTTPException, Depends, Header
from app.schemas.jira import JiraConnectRequest, JiraConnection
from app.schemas.sync import SyncStatusResponse
from app.services.jira_sync import JiraSyncService, JiraSyncError
from app.services.jira_service import JiraService
from app.services.jira_client import JiraAPIClient
from app.utils.storage import is_cache_valid, cleanup_old_files, get_issue_dir

router = APIRouter(prefix="/integrations/jira", tags=["Jira & ML Integration"])
logger = logging.getLogger(__name__)

def get_jira_client(
    x_jira_domain: str = Header(...),
    x_jira_email: str = Header(...),
    x_jira_token: str = Header(...)
) -> JiraAPIClient:
    return JiraAPIClient(x_jira_domain, x_jira_email, x_jira_token)

def get_sync_service(client: JiraAPIClient = Depends(get_jira_client)) -> JiraSyncService:
    return JiraSyncService(client)

def get_jira_service(client: JiraAPIClient = Depends(get_jira_client)) -> JiraService:
    return JiraService(client)

@router.post("/connect", response_model=JiraConnection, summary="Проверить подключение к Jira")
async def connect_jira(body: JiraConnectRequest, service: JiraService = Depends(get_jira_service)):
    status = await service.get_connection_status()
    if not status.connected:
        raise HTTPException(status_code=401, detail="Invalid Jira credentials")
    return status

@router.post(
    "/issues/{issue_key}/sync-files",
    response_model=SyncStatusResponse,
    status_code=200,
    summary="Синхронизировать вложения задачи для ML",
    description="Скачивает файлы из Jira и сохраняет локально. Если файлы уже есть, вернет путь без запроса к Jira.",
    responses={
        200: {"description": "Успешная синхронизация или использование кеша."},
        502: {"description": "Ошибка связи с Jira (после 2 попыток)."}
    }
)
async def trigger_sync(
    issue_key: str,
    force: bool = False,
    service: JiraSyncService = Depends(get_sync_service)
):
    local_dir = get_issue_dir(issue_key)
    if not force and is_cache_valid(issue_key):
        return SyncStatusResponse(status="ready", source="cache", path=str(local_dir))

    try:
        await service.sync_issue_files(issue_key)
        cleanup_old_files(days=7)
        return SyncStatusResponse(
            status="ready",
            source="jira",
            path=str(local_dir),
            message="Files successfully downloaded and processed."
        )
    except JiraSyncError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("Sync failed")
        raise HTTPException(status_code=502, detail="Internal sync error")