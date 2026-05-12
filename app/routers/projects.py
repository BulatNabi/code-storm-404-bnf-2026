from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.db import get_db
from app.services.jira_client import JiraAPIClient

router = APIRouter(prefix="/projects", tags=["Jira Projects"])

@router.get(
    "/",
    summary="Список всех подключенных досок",
    description="Возвращает массив досок для отображения селектора на фронтенде"
)
async def list_all_projects(db=Depends(get_db)):
    async with db as conn:
        cursor = await conn.execute("SELECT key, name FROM projects ORDER BY key")
        rows = await cursor.fetchall()
    
    # Преобразуем в чистый JSON-массив
    return [{"key": row["key"], "name": row["name"]} for row in rows]


class ProjectRegisterRequest(BaseModel):
    key: str      # Например: COD
    name: str     # Например: Code Storm Demo
    domain: str   # mycompany.atlassian.net
    email: str
    api_token: str

@router.post("/register", summary="Добавить доску Jira")
async def register_project(body: ProjectRegisterRequest, db=Depends(get_db)):
    # 1. Проверяем доступ к Jira
    client = JiraAPIClient(body.domain, body.email, body.api_token)
    try:
        await client._request("GET", f"/project/{body.key}")
    except Exception as e:
        print(f"❌ Jira check failed: {e}")
        raise HTTPException(status_code=403, detail="Cannot access Jira project. Check credentials.")
    
    # 2. Сохраняем в БД (без async with!)
    await db.execute(
        "INSERT OR REPLACE INTO projects VALUES (?, ?, ?, ?, ?)",
        (body.key, body.name, body.domain, body.email, body.api_token)
    )
    await db.commit()
    
    return {"status": "registered", "key": body.key}

@router.get("/{project_key}/issues", summary="Список задач доски")
async def get_project_issues(project_key: str, db=Depends(get_db)):
    # Без async with — используем db напрямую
    cursor = await db.execute("SELECT * FROM projects WHERE key=?", (project_key,))
    proj = await cursor.fetchone()
    
    if not proj:
        raise HTTPException(status_code=404, detail="Project not registered")
    
    client = JiraAPIClient(proj["domain"], proj["email"], proj["api_token"])
    issues = await client.search_issues(project_key, limit=20)
    
    return [
        {
            "key": i["key"],
            "summary": i["fields"].get("summary", ""),
            "has_attachments": bool(i["fields"].get("attachment")),
            "status": i["fields"].get("status", {}).get("name", "Unknown")
        }
        for i in issues
    ]