from fastapi import FastAPI
from pydantic import BaseModel
from celery.result import AsyncResult
import logging

from db import init_db, get_recent_traces
from tasks import run_agent_task, celery_app
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger("agent_api")

app = FastAPI()


class RunRequest(BaseModel):
    prompt: str
    bypass_cache: bool = False


@app.on_event("startup")
async def startup():
    await init_db()
    logger.info("api startup complete")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/agent/run")
async def run_agent(req: RunRequest):
    task = run_agent_task.delay(req.prompt, req.bypass_cache)
    logger.info("agent run queued", extra={"task_id": task.id, "bypass_cache": req.bypass_cache})
    return {"task_id": task.id, "status": "queued"}


@app.get("/agent/status/{task_id}")
async def get_status(task_id: str):
    result = AsyncResult(task_id, app=celery_app)

    if result.state == "PENDING":
        return {"task_id": task_id, "status": "pending"}
    if result.state == "SUCCESS":
        return {"task_id": task_id, "status": "success", "result": result.result}
    if result.state == "FAILURE":
        logger.warning("agent run failed", extra={"task_id": task_id, "error": str(result.result)})
        return {"task_id": task_id, "status": "failure", "error": str(result.result)}

    return {"task_id": task_id, "status": result.state}


@app.get("/traces")
async def list_traces(limit: int = 20):
    return await get_recent_traces(limit)