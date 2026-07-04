from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger
import requests
import sqlite3
import time
import uuid
import os
import logging

from cache import get_cached, set_cached
from logging_config import JsonFormatter

logger = logging.getLogger("agent_worker")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
MODEL_NAME = os.environ.get("MODEL_NAME", "llama3.2")
DB_PATH = "traces.db"

celery_app = Celery(
    "worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
)


@after_setup_logger.connect
@after_setup_task_logger.connect
def use_json_formatter(logger, *args, **kwargs):
    """Celery configures its own logging handlers when the worker starts,
    which happens AFTER this module is imported - so setting up logging
    at import time gets silently overwritten. Hooking into Celery's own
    setup signals instead lets us apply our JSON formatter to whatever
    handlers Celery actually ends up using."""
    for handler in logger.handlers:
        handler.setFormatter(JsonFormatter())


def ensure_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
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
    )
    cols = [row[1] for row in conn.execute("PRAGMA table_info(traces)").fetchall()]
    if "cache_hit" not in cols:
        conn.execute("ALTER TABLE traces ADD COLUMN cache_hit INTEGER DEFAULT 0")
    if "run_id" not in cols:
        conn.execute("ALTER TABLE traces ADD COLUMN run_id TEXT")
    if "step" not in cols:
        conn.execute("ALTER TABLE traces ADD COLUMN step INTEGER DEFAULT 1")
    conn.commit()
    conn.close()


ensure_table()
logger.info("traces table ready")


def log_trace_sync(prompt, response, model, latency_ms, prompt_tokens, completion_tokens, cache_hit, run_id, step):
    trace_id = str(uuid.uuid4())
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO traces
            (id, run_id, step, prompt, response, model, latency_ms, prompt_tokens, completion_tokens, cache_hit, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trace_id, run_id, step, prompt, response, model, latency_ms, prompt_tokens, completion_tokens, int(cache_hit), created_at),
    )
    conn.commit()
    conn.close()
    return trace_id


def call_ollama(prompt: str, bypass_cache: bool = False):
    start = time.perf_counter()

    if not bypass_cache:
        cached = get_cached(MODEL_NAME, prompt)
        if cached is not None:
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.info("cache hit", extra={"model": MODEL_NAME})
            return {
                "response": cached["response"],
                "prompt_tokens": cached["prompt_tokens"],
                "completion_tokens": cached["completion_tokens"],
                "latency_ms": latency_ms,
                "cache_hit": True,
            }

    payload = {"model": MODEL_NAME, "prompt": prompt, "stream": False}
    response = requests.post(OLLAMA_URL, json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()
    latency_ms = int((time.perf_counter() - start) * 1000)

    result = {
        "response": data.get("response"),
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "completion_tokens": data.get("eval_count", 0),
        "latency_ms": latency_ms,
        "cache_hit": False,
    }

    if not bypass_cache:
        set_cached(MODEL_NAME, prompt, {
            "response": result["response"],
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"],
        })

    logger.info("ollama call completed", extra={"model": MODEL_NAME, "latency_ms": latency_ms})
    return result


@celery_app.task(
    name="run_agent_task",
    bind=True,
    autoretry_for=(requests.exceptions.RequestException,),
    retry_backoff=5,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def run_agent_task(self, prompt: str, bypass_cache: bool = False):
    run_id = str(uuid.uuid4())
    logger.info("agent run started", extra={"run_id": run_id, "attempt": self.request.retries + 1})

    try:
        plan_prompt = (
            "You are an assistant planning how to answer a question. "
            "In 2-3 short bullet points, outline your approach. "
            "Do not answer the question yet.\n\n"
            f"Question: {prompt}"
        )
        step1 = call_ollama(plan_prompt, bypass_cache=bypass_cache)
        trace_id_1 = log_trace_sync(
            prompt=plan_prompt,
            response=step1["response"],
            model=MODEL_NAME,
            latency_ms=step1["latency_ms"],
            prompt_tokens=step1["prompt_tokens"],
            completion_tokens=step1["completion_tokens"],
            cache_hit=step1["cache_hit"],
            run_id=run_id,
            step=1,
        )

        answer_prompt = (
            f"Question: {prompt}\n\n"
            f"Your plan:\n{step1['response']}\n\n"
            "Now give the final answer to the question directly, without repeating the plan."
        )
        step2 = call_ollama(answer_prompt, bypass_cache=bypass_cache)
        trace_id_2 = log_trace_sync(
            prompt=answer_prompt,
            response=step2["response"],
            model=MODEL_NAME,
            latency_ms=step2["latency_ms"],
            prompt_tokens=step2["prompt_tokens"],
            completion_tokens=step2["completion_tokens"],
            cache_hit=step2["cache_hit"],
            run_id=run_id,
            step=2,
        )

    except requests.exceptions.RequestException as e:
        # Transient network/connection issue (e.g. Ollama still warming up,
        # briefly unreachable). Let Celery's autoretry handle backing off
        # and retrying, rather than failing the run outright.
        logger.warning("transient failure, will retry", extra={"run_id": run_id, "error": str(e)})
        raise

    total_latency_ms = step1["latency_ms"] + step2["latency_ms"]
    logger.info("agent run completed", extra={"run_id": run_id, "total_latency_ms": total_latency_ms})

    return {
        "run_id": run_id,
        "prompt": prompt,
        "plan": step1["response"],
        "response": step2["response"],
        "model": MODEL_NAME,
        "total_latency_ms": total_latency_ms,
        "steps": [
            {"step": 1, "trace_id": trace_id_1, "latency_ms": step1["latency_ms"], "cache_hit": step1["cache_hit"]},
            {"step": 2, "trace_id": trace_id_2, "latency_ms": step2["latency_ms"], "cache_hit": step2["cache_hit"]},
        ],
    }