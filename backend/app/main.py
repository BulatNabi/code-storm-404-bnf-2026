from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app import models  # noqa: F401 — registers all models with Base
from app.routers import auth, projects, analysis, jira

app = FastAPI(
    title="Финтех-регуляторный радар",
    description=(
        "API для анализа регуляторных рисков продуктовых фич. "
        "Принимает user story или файл (PDF/DOCX) и возвращает "
        "затронутые регуляторные зоны, чеклист и список документов к обновлению."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(jira.router, prefix="/api")


@app.get("/", tags=["Health"])
async def health():
    return {"status": "ok", "service": "fintech-radar-backend"}
