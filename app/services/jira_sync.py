import logging
from typing import List
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryError
import httpx
from app.services.jira_client import JiraAPIClient
from app.utils.storage import save_file_locally

logger = logging.getLogger(__name__)

class JiraSyncError(Exception):
    pass

class JiraSyncService:
    def __init__(self, jira_client: JiraAPIClient):
        self.client = jira_client

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    async def download_attachment_safely(self, issue_key: str, att_id: str, filename: str):
        try:
            content = bytearray()
            async for chunk in self.client.stream_attachment_content(att_id):
                content.extend(chunk)
            return save_file_locally(issue_key, filename, content)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise JiraSyncError(f"Attachment {att_id} not found in Jira")
            raise
        except Exception as e:
            logger.warning(f"Attempt failed for {att_id}: {e}")
            raise

    async def sync_issue_files(self, issue_key: str) -> List[str]:
        attachments = await self.client.get_attachment_metadata(issue_key)
        if not attachments:
            return []

        local_paths = []
        for att in attachments:
            try:
                path = await self.download_attachment_safely(
                    issue_key=issue_key,
                    att_id=att["id"],
                    filename=att["filename"]
                )
                local_paths.append(path)
            except RetryError:
                logger.error(f"Jira unavailable after 2 attempts for {att['id']}")
                raise JiraSyncError("Jira service unavailable. Failed after 2 retries.")
            except JiraSyncError as e:
                raise e
        return local_paths