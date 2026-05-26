import base64
import logging
from typing import List

import httpx

logger = logging.getLogger(__name__)


class JiraAPIClient:
    def __init__(self, domain: str, email: str, api_token: str):
        self.base_url = f"https://{domain}/rest/api/3"
        credentials = f"{email}:{api_token}"
        encoded = base64.b64encode(credentials.encode("ascii")).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {encoded}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.timeout = httpx.Timeout(30.0)

    async def _request(self, method: str, endpoint: str, **kwargs):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=self.headers,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()

    async def verify_connection(self) -> bool:
        try:
            await self._request("GET", "/myself")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return False
            raise

    async def search_project_issues(self, project_key: str, limit: int = 50) -> List[dict]:
        """Все задачи проекта (доски) по ключу проекта."""
        params = {
            "jql": f'project = "{project_key}" ORDER BY updated DESC',
            "maxResults": limit,
            "fields": "summary,status,issuetype,updated,attachment,assignee",
        }
        data = await self._request("POST", "/search/jql", json=params)
        return data.get("issues", [])

    async def get_issue_details(self, issue_key: str) -> dict:
        return await self._request("GET", f"/issue/{issue_key}", params={"expand": "attachment"})

    async def post_comment(self, issue_key: str, text: str) -> None:
        """Постит комментарий к таске в формате Atlassian Document Format."""
        body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": text}],
                    }
                ],
            }
        }
        await self._request("POST", f"/issue/{issue_key}/comment", json=body)

    async def download_attachment(self, attachment_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/attachment/content/{attachment_id}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.content
