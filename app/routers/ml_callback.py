from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.db import get_db
from app.services.jira_client import JiraAPIClient
from app.utils.jira_formatter import md_to_jira_adf

router = APIRouter(prefix="/ml", tags=["ML Integration"])

class MLSummaryRequest(BaseModel):
    issue_key: str      # Например: COD-12
    summary_md: str     # Текст от ML (10-150 символов)

@router.post("/summary", summary="Записать результат ML в комментарий Jira")
async def receive_ml_summary(body: MLSummaryRequest, db=Depends(get_db)):
    # 1. Извлекаем ключ проекта из ключа задачи (COD-12 -> COD)
    project_key = body.issue_key.split("-")[0]
    
    # 2. Достаём креды
    async with db as conn:
        cursor = await conn.execute("SELECT * FROM projects WHERE key=?", (project_key,))
        proj = await cursor.fetchone()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found in DB")
    
    # 3. Формируем ADF и отправляем в Jira
    client = JiraAPIClient(proj["domain"], proj["email"], proj["api_token"])
    adf_body = md_to_jira_adf(body.summary_md)
    
    try:
        await client._request(
            "POST",
            f"/issue/{body.issue_key}/comment",
            json={"body": adf_body}
        )
        return {"status": "commented", "issue": body.issue_key, "length": len(body.summary_md)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Jira comment failed: {str(e)}")