import aiosqlite
import time
import uuid

DB_PATH = "traces.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    response TEXT,
    model TEXT NOT NULL,
    latency_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    created_at TEXT NOT NULL
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()


async def log_trace(prompt, response, model, latency_ms, prompt_tokens, completion_tokens):
    trace_id = str(uuid.uuid4())
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO traces
                (id, prompt, response, model, latency_ms, prompt_tokens, completion_tokens, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trace_id, prompt, response, model, latency_ms, prompt_tokens, completion_tokens, created_at),
        )
        await db.commit()

    return trace_id


async def get_recent_traces(limit=20):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM traces ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]