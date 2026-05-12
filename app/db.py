# app/db.py
import aiosqlite
from pathlib import Path

DB_PATH = Path("data/jira_backend.db")

async def init_db():
    """Инициализация БД при старте (один раз)"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                key TEXT PRIMARY KEY,
                name TEXT,
                domain TEXT,
                email TEXT,
                api_token TEXT
            )
        """)
        await db.commit()

async def get_db():
    """
    Зависимость FastAPI: возвращает готовое соединение.
    НЕ используй `async with` внутри — только yield + close.
    """
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()