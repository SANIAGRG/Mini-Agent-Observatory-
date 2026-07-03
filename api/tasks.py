from celery import Celery
import requests
import sqlite3
import time
import uuid
import os

# Falls back to localhost for native/manual runs (Phase 3 style).
# In Docker, docker-compose.yml overrides these so the container can
# reach Redis by service name, and Ollama on the host machine.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
MODEL_NAME = os.environ.get("MODEL_NAME", "llama3.2")
DB_PATH = "traces.db"

celery_app = Celery(
    "worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
)


def ensure_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
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
    )
    conn.commit()
    conn.close()


ensure_table()


def log_trace_sync(prompt, response, model, latency_ms, prompt_tokens, completion_tokens):
    trace_id = str(uuid.uuid4())
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO traces
            (id, prompt, response, model, latency_ms, prompt_tokens, completion_tokens, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trace_id, prompt, response, model, latency_ms, prompt_tokens, completion_tokens, created_at),
    )
    conn.commit()
    conn.close()
    return trace_id


@celery_app.task(name="run_agent_task")
def run_agent_task(prompt: str):
    payload = {"model": MODEL_NAME, "prompt": prompt, "stream": False}

    start = time.perf_counter()
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()
    latency_ms = int((time.perf_counter() - start) * 1000)

    prompt_tokens = data.get("prompt_eval_count", 0)
    completion_tokens = data.get("eval_count", 0)

    trace_id = log_trace_sync(
        prompt=prompt,
        response=data.get("response"),
        model=MODEL_NAME,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )

    return {
        "trace_id": trace_id,
        "prompt": prompt,
        "response": data.get("response"),
        "model": MODEL_NAME,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }