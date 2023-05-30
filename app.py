from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from utils.config import config
from utils.redis_handler import get_async_redis_conn, wait_for_message, queue_high, queue_low, redis_conn
from utils.logger import log
from typing import Any
from rq.worker import Worker

from utils.thumbnail import generate_thumbnail, get_best_time_key, get_latest_thumbnail_from_files, get_job_id, get_thumbnail_from_files

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Timestamp", "X-Title"]
)

@app.get("/api/v1/getThumbnail")
async def get_thumbnail(response: Response, videoID: str, time: float | None = None,
                        generateNow: bool = False, title: str | None = None,
                        officialTime: bool = False) -> Response:
    if type(videoID) is not str or (type(time) is not float and time is not None) \
            or type(generateNow) is not bool:
        raise HTTPException(status_code=400, detail="Invalid parameters")

    if officialTime and time is not None:
        await (await get_async_redis_conn()).set(get_best_time_key(videoID), time)

    try:
        return await handle_thumbnail_response(videoID, time, title, response)
    except FileNotFoundError:
        pass

    if time is None:
        # If we got here with a None time, then there is no thumbnail to pull from
        raise HTTPException(status_code=204, detail="Thumbnail not cached")
    

    job_id = get_job_id(videoID, time)
    queue = queue_high if generateNow else queue_low

    job = queue.fetch_job(job_id)
    other_queue_job = queue_low.fetch_job(job_id) if queue == queue_high else queue_high.fetch_job(job_id)
    if other_queue_job is not None:
        if other_queue_job.is_started:
            # It is already started, use it
            job = other_queue_job
        elif queue == queue_high:
            # Old queue is low, prefer new one
            queue_low.remove(other_queue_job)
        elif job is not None:
            # New queue is low, old queue is high, prefer old one
            queue.remove(job)
            job = other_queue_job
        else:
            # New queue is low, old queue is high, prefer old one
            job = other_queue_job


    if job is None or job.is_finished or job.is_failed:
        # Start the job if it is not already started
        job = queue.enqueue(generate_thumbnail,
                        args=(videoID, time, title), job_id=job_id)
    
    result: bool = False
    if generateNow or ((job.get_position() or 0) < config["thumbnail_storage"]["max_before_async_generation"]
            and len(queue_high) < config["thumbnail_storage"]["max_before_async_generation"]):
        try:
            result = (await wait_for_message(job_id)) == "true"
        except TimeoutError:
            log("Failed to generate thumbnail due to timeout")
            raise HTTPException(status_code=204, detail="Failed to generate thumbnail due to timeout")
    else:
        log("Thumbnail not generated yet", job.get_position())
        raise HTTPException(status_code=204, detail="Thumbnail not generated yet")

    if result:
        return await handle_thumbnail_response(videoID, time, title, response)
    else:
        log("Failed to generate thumbnail")
        raise HTTPException(status_code=204, detail="Failed to generate thumbnail")
    
async def handle_thumbnail_response(video_id: str, time: float | None, title: str | None, response: Response) -> Response:
    thumbnail = await get_thumbnail_from_files(video_id, time, title) if time is not None else await get_latest_thumbnail_from_files(video_id)
    response.headers["X-Timestamp"] = str(thumbnail.time)
    if thumbnail.title:
        response.headers["X-Title"] = thumbnail.title.strip()

    return Response(content=thumbnail.image, media_type="image/webp", headers=response.headers)

@app.get("/api/v1/status")
def get_status() -> dict[str, Any]:
    workers = Worker.all(connection=redis_conn)

    return {
        "queues": {
            "high": {
                "length": len(queue_high),
                "scheduled_jobs": queue_high.scheduled_job_registry.count,
                "finished_jobs": queue_high.finished_job_registry.count,
                "failed_jobs": queue_high.failed_job_registry.count,
                "started_jobs": queue_high.started_job_registry.count,
                "deferred_jobs": queue_high.deferred_job_registry.count,
                "cancelled_jobs": queue_high.canceled_job_registry.count,
            },
            "default": {
                "length": len(queue_low),
                "scheduled_jobs": queue_low.scheduled_job_registry.count,
                "finished_jobs": queue_low.finished_job_registry.count,
                "failed_jobs": queue_low.failed_job_registry.count,
                "started_jobs": queue_low.started_job_registry.count,
                "deferred_jobs": queue_low.deferred_job_registry.count,
                "cancelled_jobs": queue_low.canceled_job_registry.count,
            },
        },
        "workers": [get_worker_info(worker) for worker in workers],
        "workers_count": len(workers),
    }

def get_worker_info(worker: Worker) -> dict[str, Any]:
    current_job = worker.get_current_job()
    return {
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
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=config["server"]["host"], # type: ignore
                port=config["server"]["port"], reload=config["server"]["reload"],
                log_level="info" if config["debug"] else "warning")