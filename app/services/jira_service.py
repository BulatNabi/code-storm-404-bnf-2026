from datetime import datetime
from app.schemas.jira import (
    JiraConnection, JiraIssue, JiraIssueListResponse, 
    JiraIssueDetail, JiraAttachment
)
from .jira_client import JiraAPIClient

class JiraService:
    def __init__(self, client: JiraAPIClient):
        self.client = client

    async def get_connection_status(self) -> JiraConnection:
        is_connected = await self.client.verify_connection()
        # В реальном приложении домен/почему лучше брать из защищенного хранилища (session/DB)
        return JiraConnection(
            connected=is_connected,
            domain=self.client.base_url.split("//")[1].split("/rest")[0], 
            email=None, 
            connected_at=datetime.utcnow().isoformat() + "Z" if is_connected else None
        )

    async def list_issues(self, query: str = "") -> JiraIssueListResponse:
        raw_issues = await self.client.search_user_issues(query)
        
        items = []
        for issue in raw_issues:
            fields = issue.get("fields", {})
            # Jira может вернуть вложения как список объектов или null
            attachments = fields.get("attachment") or []
            
            items.append(JiraIssue(
                key=issue.get("key"),
                summary=fields.get("summary"),
                status=fields.get("status", {}).get("name"),
                issue_type=fields.get("issuetype", {}).get("name"),
                assignee=fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
                updated_at=fields.get("updated"),
                has_attachments=len(attachments) > 0
            ))
            
        return JiraIssueListResponse(items=items, total=len(items))

    async def get_issue_detail(self, issue_key: str) -> JiraIssueDetail:
        raw = await self.client.get_issue_details(issue_key)
        fields = raw.get("fields", {})
        
        # Маппинг вложений
        attachments = []
        for att in (fields.get("attachment") or []):
            attachments.append(JiraAttachment(
                id=str(att.get("id")),
                filename=att.get("filename"),
                size=att.get("size"),
                mime_type=att.get("mimeType"),
                url=att.get("content") # Прямая ссылка на контент
            ))

        # Описание в Jira v3 часто приходит в формате ADF (Atlassian Document Format),
        # но для простоты пока берем как есть или конвертируем в текст при необходимости.
        description = fields.get("description")
        if isinstance(description, dict):
            # Простая экстракция текста из ADF, если нужно
            description = " ".join(
                block.get("content", [{}])[0].get("text", "") 
                for block in description.get("content", [])
            )

        return JiraIssueDetail(
            key=raw.get("key"),
            summary=fields.get("summary"),
            description=description or "",
            status=fields.get("status", {}).get("name"),
            issue_type=fields.get("issuetype", {}).get("name"),
            attachments=attachments
        )