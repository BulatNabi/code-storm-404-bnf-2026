import base64
import logging
from typing import Optional, List
import httpx
from app.schemas.jira import JiraAttachment, JiraIssue, JiraIssueDetail

logger = logging.getLogger(__name__)

class JiraAPIClient:
    def __init__(self, domain: str, email: str, api_token: str):
        self.base_url = f"https://{domain}/rest/api/3"
        # Формируем заголовок авторизации: Basic base64(email:token) [[3]]
        credentials = f"{email}:{api_token}"
        encoded_credentials = base64.b64encode(credentials.encode("ascii")).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Accept": "application/json"
        }
        # Таймауты важны для stability в embedded/startup проектах
        self.timeout = httpx.Timeout(30.0)

    async def get_available_projects(self) -> list[dict]:
        """
        Получает все проекты (доски), доступные пользователю в данном домене Jira.
        Использует endpoint /rest/api/3/project
        """
        resp = await self._request("GET", "/project")
        # Jira возвращает список объектов, нам нужны только key, name, id
        return [
            {"key": p["key"], "name": p["name"], "id": p["id"]}
            for p in resp if p.get("key") and p.get("name")
        ]


    async def _request(self, method: str, endpoint: str, **kwargs):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method, 
                f"{self.base_url}{endpoint}", 
                headers=self.headers, 
                **kwargs
            )
            response.raise_for_status()
            return response.json()

    async def verify_connection(self) -> bool:
        """Проверяем валидность кредов через эндпоинт /myself"""
        try:
            await self._request("GET", "/myself")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return False
            raise

    async def search_user_issues(self, query: str = "", limit: int = 50) -> List[dict]:
        """
        Ищем задачи, назначенные на текущего пользователя.
        Используем /rest/api/3/search/jql с параметром fields для оптимизации трафика [[47]].
        """
        jql = f"assignee = currentUser() AND status != Done ORDER BY updated DESC"
        if query:
            jql += f" AND summary ~ '{query}'"
        
        # Явно запрашиваем нужные поля, чтобы не тянуть лишние данные
        fields = "summary,status,issuetype,updated,attachment"
        params = {"jql": jql, "maxResults": limit, "fields": fields}
        
        data = await self._request("POST", "/search/jql", json=params)
        return data.get("issues", [])

    async def get_issue_details(self, issue_key: str) -> dict:
        """Получаем детали задачи с расширениями для вложений [[21]][[51]]"""
        # expand=attachment нужен, чтобы получить метаданные вложений в ответе
        return await self._request("GET", f"/issue/{issue_key}", params={"expand": "attachment"})

    async def download_attachment_content(self, attachment_id: str) -> bytes:
        """Скачиваем бинарный контент вложения [[50]]"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/attachment/content/{attachment_id}",
                headers=self.headers
            )
            response.raise_for_status()
            return response.content