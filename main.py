from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.db import init_db
from app.routers import projects, jira, ml_callback

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация SQLite при запуске
    await init_db()
    yield

app = FastAPI(
    title="Code Storm 404: Jira -> ML Backend",
    description="Хакатонный пайплайн: Jira Boards → Files Sync → ML Analysis → Auto Comments",
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(projects.router)
app.include_router(jira.router)
app.include_router(ml_callback.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)