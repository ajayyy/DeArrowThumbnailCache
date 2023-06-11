import threading
from typing import Any
from fastapi import FastAPI, HTTPException
import uvicorn
from rq.worker import SimpleWorker as Worker, WorkerStatus, DequeueStrategy
from utils.redis_handler import redis_conn
from utils.config import config

listen = ["high", "default"]
worker = Worker(listen, connection=redis_conn)

health_check = FastAPI()

@health_check.get("{full_path:path}")
def get_health_check() -> dict[str, Any]:
    if worker.state == WorkerStatus.SUSPENDED:
        raise HTTPException(status_code=500, detail="Worker suspended")
    
    current_job = worker.get_current_job()
    return {
        "name": worker.name,
        "key": worker.key,
        "state": worker.state,
        "current_job": {
            "id": current_job.id,
            "description": current_job.description,
            "origin": current_job.origin,
            "created_at": current_job.created_at,
            "enqueued_at": current_job.enqueued_at,
            "started_at": current_job.started_at,
            "ended_at": current_job.ended_at,
            "exc_info": current_job.exc_info,
            "meta": current_job.meta,
        } if current_job is not None else None,
        "birth_date": worker.birth_date,
        "successful_job_count": worker.successful_job_count,
        "failed_job_count": worker.failed_job_count,
        "total_working_time": worker.total_working_time,
        "total_workers": worker.count(redis_conn)
    }
    
if __name__ == "__main__":
    uvicorn_thread = threading.Thread(target=uvicorn.run, kwargs={
        "app": health_check,
        "host": config["server"]["host"], # type: ignore
        "port": config["server"]["worker_health_check_port"],
        "log_level": "info" if config["debug"] else "warning"
    })
    uvicorn_thread.daemon = True
    uvicorn_thread.start()

    worker.work(dequeue_strategy=DequeueStrategy.ROUND_ROBIN)