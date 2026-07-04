import aiosqlite
import time
import uuid

DB_PATH = "traces.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    step INTEGER DEFAULT 1,
    prompt TEXT NOT NULL,
    response TEXT,
    model TEXT NOT NULL,
    latency_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cache_hit INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TABLE_SQL)

        # Migration for traces.db files created before Phase 5 / Phase 7.
        cursor = await db.execute("PRAGMA table_info(traces)")
        cols = [row[1] for row in await cursor.fetchall()]
        if "cache_hit" not in cols:
            await db.execute("ALTER TABLE traces ADD COLUMN cache_hit INTEGER DEFAULT 0")
        if "run_id" not in cols:
            await db.execute("ALTER TABLE traces ADD COLUMN run_id TEXT")
        if "step" not in cols:
            await db.execute("ALTER TABLE traces ADD COLUMN step INTEGER DEFAULT 1")

        await db.commit()


async def log_trace(prompt, response, model, latency_ms, prompt_tokens, completion_tokens,
                     cache_hit=False, run_id=None, step=1):
    trace_id = str(uuid.uuid4())
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO traces
                (id, run_id, step, prompt, response, model, latency_ms, prompt_tokens, completion_tokens, cache_hit, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trace_id, run_id, step, prompt, response, model, latency_ms, prompt_tokens, completion_tokens, int(cache_hit), created_at),
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