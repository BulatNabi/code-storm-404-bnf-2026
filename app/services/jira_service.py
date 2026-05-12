import logging
from app.services.jira_client import JiraAPIClient
from app.schemas.jira import JiraConnection

logger = logging.getLogger(__name__)

class JiraService:
    def __init__(self, client: JiraAPIClient):
        self.client = client

    async def get_connection_status(self) -> JiraConnection:
        try:
            await self.client._request("GET", "/myself")
            return JiraConnection(connected=True, domain=self.client.base_url.split("//")[1].split("/rest")[0])
        except Exception:
            return JiraConnection(connected=False)