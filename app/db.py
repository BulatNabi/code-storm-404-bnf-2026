import aiosqlite
from pathlib import Path
from contextlib import asynccontextmanager

DB_PATH = Path("data/jira_backend.db")

async def init_db():
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

@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db