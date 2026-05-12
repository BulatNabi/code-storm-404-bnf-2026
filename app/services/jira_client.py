import base64
import logging
from typing import List, AsyncGenerator
import httpx

logger = logging.getLogger(__name__)

class JiraAPIClient:
    def __init__(self, domain: str, email: str, api_token: str):
        self.base_url = f"https://{domain}/rest/api/3"
        credentials = f"{email}:{api_token}"
        encoded = base64.b64encode(credentials.encode("ascii")).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {encoded}",
            "Accept": "application/json"
        }
        self.timeout = httpx.Timeout(30.0)
    
    async def search_issues(self, project_key: str, limit: int = 20) -> list[dict]:
        """Поиск задач по проекту через JQL"""
        jql = f'project = "{project_key}" ORDER BY updated DESC'
        params = {"jql": jql, "maxResults": limit, "fields": "key,summary,attachment"}
        resp = await self._request("GET", "/search", params=params)
        return resp.get("issues", [])

    async def _request(self, method: str, endpoint: str, **kwargs):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(method, f"{self.base_url}{endpoint}", headers=self.headers, **kwargs)
            resp.raise_for_status()
            return resp.json()

    async def get_issue_details(self, issue_key: str) -> dict:
        return await self._request("GET", f"/issue/{issue_key}", params={"fields": "attachment", "expand": "attachment"})

    async def get_attachment_metadata(self, issue_key: str) -> List[dict]:
        data = await self.get_issue_details(issue_key)
        return data.get("fields", {}).get("attachment", [])

    async def stream_attachment_content(self, attachment_id: str) -> AsyncGenerator[bytes, None]:
        url = f"{self.base_url}/attachment/content/{attachment_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("GET", url, headers=self.headers) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    yield chunk