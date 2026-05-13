from fastapi import APIRouter, HTTPException, Header, Depends
from app.schemas.jira import (
    JiraConnectRequest, JiraConnection, JiraIssueListResponse,
    JiraIssueDetail, JiraImportRequest
)
from app.services.jira_client import JiraAPIClient
from app.services.jira_service import JiraService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations/jira", tags=["Jira Integration"])

# Helper для получения клиента из заголовков или сессии
def get_jira_service(
    x_jira_domain: str = Header(...), 
    x_jira_email: str = Header(...), 
    x_jira_token: str = Header(...)
) -> JiraService:
    client = JiraAPIClient(x_jira_domain, x_jira_email, x_jira_token)
    return JiraService(client)

@router.get("", response_model=JiraConnection)
async def get_jira_status(service: JiraService = Depends(get_jira_service)):
    return await service.get_connection_status()

@router.post("/connect", response_model=JiraConnection)
async def connect_jira(body: JiraConnectRequest, service: JiraService = Depends(get_jira_service)):
    # Здесь можно сохранить токены в зашифрованном виде в БД
    status = await service.get_connection_status()
    if not status.connected:
        raise HTTPException(status_code=401, detail="Invalid Jira credentials")
    return status

@router.get("/issues", response_model=JiraIssueListResponse)
async def list_jira_issues(q: str = "", service: JiraService = Depends(get_jira_service)):
    try:
        return await service.list_issues(q)
    except Exception as e:
        logger.error(f"Jira API error: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch issues from Jira")

@router.get("/issues/{issue_key}", response_model=JiraIssueDetail)
async def get_jira_issue(issue_key: str, service: JiraService = Depends(get_jira_service)):
    try:
        return await service.get_issue_detail(issue_key)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Issue {issue_key} not found")

@router.post("/issues/{issue_key}/import")
async def import_jira_attachments(
    issue_key: str, 
    body: JiraImportRequest, 
    service: JiraService = Depends(get_jira_service)
):
    """
    Проксируем файлы из Jira. 
    Возвращаем метаданные для отправки на /analyze.
    """
    imported = []
    for att_id in body.attachment_ids:
        try:
            # В реальном сценарии здесь можно стримить файл, 
            # а не грузить его целиком в память (bytes)
            content = await service.client.download_attachment_content(att_id)
            
            imported.append({
                "attachment_id": att_id,
                # filename лучше получить из get_issue_detail заранее и передать в body,
                # либо сделать лишний запрос, если критично.
                "filename": f"{issue_key}_{att_id}.bin", 
                "size": len(content),
                "content_preview": content[:100].hex() # Пример: отдаем хекс-превью для анализа
            })
        except Exception:
            logger.warning(f"Failed to download attachment {att_id}")
            continue
            
    return {"issue_key": issue_key, "imported": imported}