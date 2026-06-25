import json
import traceback
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from utils.config import config
from utils.floatie import fetch_video_data
from utils.proxy import get_proxy_url
from utils.redis_handler import wait_for_message, queue_high, queue_low, redis_conn
from utils.logger import log
from typing import Any
import time
from hmac import compare_digest
from rq.worker import Worker
from rq.job import Job
from utils.test_utils import in_test
import logging

from utils.thumbnail import generate_thumbnail, generate_nebula_thumbnail, \
    get_latest_thumbnail_from_files, get_job_id, get_thumbnail_from_files, set_best_time
from utils.video import valid_video_id
from utils.nebula import valid_nebula_slug

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Timestamp", "X-Title", "X-Failure-Reason"],
    max_age=86400,
)

logger = logging.getLogger('uvicorn.error')

@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("https://github.com/ajayyy/DeArrowThumbnailCache")

@app.get("/api/v1/getThumbnail")
async def get_thumbnail(response: Response, request: Request,
                        videoID: str, time: float | None = None,
                        generateNow: bool = False,
                        title: str | None = None,
                        officialTime: bool = False,
                        isLivestream: bool = False,
                        redirectUrl: str | None = None) -> Response:
    if type(videoID) is not str or (type(time) is not float and time is not None) \
            or type(generateNow) is not bool or not valid_video_id(videoID):
        raise HTTPException(status_code=400, detail="Invalid parameters")

    if officialTime and time is not None:
        await set_best_time(videoID, time)

    try:
        return await handle_thumbnail_response(videoID, time, isLivestream, title, response)
    except FileNotFoundError:
        pass

    if time is None:
        # If we got here with a None time, then there is no thumbnail to pull from
        return thumbnail_response_error(redirectUrl, "Thumbnail not cached")

    try:
        result = await _enqueue_and_wait(
            job_id=get_job_id(videoID, time),
            generate_now=generateNow,
            request=request,
            enqueue_fn=generate_thumbnail,
            enqueue_args=(videoID, time, title, isLivestream, not in_test()),
            label="YouTube",
        )
    except TimeoutError:
        return thumbnail_response_error(redirectUrl, "Failed to generate thumbnail due to timeout")

    if result is None:
        return thumbnail_response_error(redirectUrl, "Thumbnail not generated yet")
    if result:
        try:
            return await handle_thumbnail_response(videoID, time, isLivestream, title, response)
        except Exception as e:
            log("Server error when getting thumbnails", e)
            return thumbnail_response_error(redirectUrl, "Server error")
    else:
        return thumbnail_response_error(redirectUrl, "Failed to generate thumbnail")


async def handle_thumbnail_response(video_id: str, time: float | None, is_livestream: bool, title: str | None, response: Response) -> Response:
    thumbnail = await get_thumbnail_from_files(video_id, time, is_livestream, title) if time is not None else \
        await get_latest_thumbnail_from_files(video_id, is_livestream)
    response.headers["X-Timestamp"] = str(thumbnail.time)
    response.headers["Cache-Control"] = "public, max-age=3600"
    if thumbnail.title is not None:
        try:
            response.headers["X-Title"] = thumbnail.title.strip()
        except UnicodeEncodeError:
            pass

    return Response(content=thumbnail.image, media_type="image/webp", headers=response.headers)

def thumbnail_response_error(redirect_url: str | None, text: str) -> Response:
    if redirect_url is not None and redirect_url.startswith("https://i.ytimg.com"):
        return RedirectResponse(redirect_url)
    else:
        raise HTTPException(status_code=204, headers={
            "X-Failure-Reason": text
        })


# ─── Shared queue management ─────────────────────────────────────────────────

async def _enqueue_and_wait(
    job_id: str,
    generate_now: bool,
    request: Request,
    enqueue_fn: Any,
    enqueue_args: tuple,
    label: str,
) -> bool | None:
    """Reconcile queue priorities, enqueue if needed, and wait for result.

    Returns ``True`` if the job succeeded, ``False`` if it failed, or
    ``None`` with a reason string if the caller should return an error
    immediately (returned as a tuple ``(None, reason)``).
    """
    queue = queue_high if generate_now else queue_low

    job = queue.fetch_job(job_id)
    other_queue_job: Job | None = (
        queue_low.fetch_job(job_id) if queue == queue_high
        else queue_high.fetch_job(job_id)
    )
    if other_queue_job is not None:
        if other_queue_job.is_started:
            job = other_queue_job
        elif queue == queue_high:
            queue_low.remove(other_queue_job)
        elif job is not None:
            queue.remove(job)
            job = other_queue_job
        else:
            job = other_queue_job

    if job is None or job.is_finished:
        if len(queue) > config["thumbnail_storage"]["max_queue_size"]:
            return None  # caller handles "queue too big"

        job = queue.enqueue(
            enqueue_fn,
            args=enqueue_args,
            job_id=job_id,
            job_timeout=30,
            failure_ttl=500,
            ttl=60,
            at_front="front_auth" in config
                and config["front_auth"] is not None
                and request.headers.get("authorization") == config["front_auth"],
        )

    if job.is_failed:
        return False

    if ((job.get_position() or 0) < config["thumbnail_storage"]["max_before_async_generation"]
            and (generate_now or len(queue_high) < config["thumbnail_storage"]["max_before_async_generation"])):
        try:
            return (await wait_for_message(job_id)) == "true"
        except TimeoutError:
            log(f"{label} thumbnail generation timed out")
            raise
    else:
        log(f"{label} thumbnail not generated yet", job.get_position())
        return None


# ─── Nebula thumbnails ────────────────────────────────────────────────────────

@app.get("/api/v1/getNebulaThumbnail")
async def get_nebula_thumbnail(response: Response, request: Request,
                               videoSlug: str, time: float | None = None,
                               generateNow: bool = False,
                               title: str | None = None,
                               officialTime: bool = False) -> Response:
    if not config.get("nebula_worker_url"):  # type: ignore[attr-defined]
        raise HTTPException(status_code=501, detail="Nebula support is not configured")

    if type(videoSlug) is not str or (type(time) is not float and time is not None) \
            or type(generateNow) is not bool or not valid_nebula_slug(videoSlug):
        raise HTTPException(status_code=400, detail="Invalid parameters")

    if officialTime and time is not None:
        await set_best_time(videoSlug, time, nebula=True)

    try:
        return await handle_nebula_thumbnail_response(videoSlug, time, title, response)
    except FileNotFoundError:
        pass

    if time is None:
        return nebula_thumbnail_response_error("Nebula thumbnail not cached")

    try:
        result = await _enqueue_and_wait(
            job_id=get_job_id(videoSlug, time, nebula=True),
            generate_now=generateNow,
            request=request,
            enqueue_fn=generate_nebula_thumbnail,
            enqueue_args=(videoSlug, time, title, not in_test()),
            label="Nebula",
        )
    except TimeoutError:
        return nebula_thumbnail_response_error("Failed to generate Nebula thumbnail due to timeout")

    if result is None:
        return nebula_thumbnail_response_error("Nebula thumbnail not generated yet")
    if result:
        try:
            return await handle_nebula_thumbnail_response(videoSlug, time, title, response)
        except Exception as e:
            log("Server error when getting Nebula thumbnails", e)
            return nebula_thumbnail_response_error("Server error")
    else:
        return nebula_thumbnail_response_error("Failed to generate Nebula thumbnail")


async def handle_nebula_thumbnail_response(
    video_slug: str, time: float | None, title: str | None, response: Response
) -> Response:
    thumbnail = (
        await get_thumbnail_from_files(video_slug, time, title=title, nebula=True)
        if time is not None
        else await get_latest_thumbnail_from_files(video_slug, nebula=True)
    )
    response.headers["X-Timestamp"] = str(thumbnail.time)
    response.headers["Cache-Control"] = "public, max-age=3600"
    if thumbnail.title is not None:
        try:
            response.headers["X-Title"] = thumbnail.title.strip()
        except UnicodeEncodeError:
            pass

    return Response(content=thumbnail.image, media_type="image/webp",
                    headers=response.headers)


def nebula_thumbnail_response_error(text: str) -> Response:
    raise HTTPException(status_code=204, headers={
        "X-Failure-Reason": text
    })

@app.get("/api/v1/status")
def get_status(includeDefault: bool = True, auth: str | None = None) -> dict[str, Any]:
    try:
        workers = Worker.all(connection=redis_conn)
        is_authorized = auth is not None and compare_digest(auth, config["status_auth_password"])

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
                } if includeDefault else None,
            },
            "workers": [get_worker_info(worker, is_authorized) for worker in workers],
            "workers_count": len(workers),
        }
    except Exception:
        logger.error(f"worker error: {traceback.format_exc()}")
        return {
            "workers": [],
            "workers_count": 0
        }

@app.get("/api/v1/clearQueue")
def clear_queue(auth: str, low: bool = True, high: bool = False) -> None:
    is_authorized = compare_digest(auth, config["status_auth_password"])

    if is_authorized:
        if low:
            queue_low.empty()
        if high:
            queue_high.empty()
    else:
        raise HTTPException(status_code=204)


def get_worker_info(worker: Worker, is_authorized: bool) -> dict[str, Any]:
    try:
        current_job = worker.get_current_job()
        return {
            "name": worker.name,
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
            } if current_job is not None and is_authorized else None,
            "birth_date": worker.birth_date,
            "successful_job_count": worker.successful_job_count,
            "failed_job_count": worker.failed_job_count,
            "total_working_time": worker.total_working_time,
        }
    except Exception:
        return {}

@app.get("/api/v1/floatie")
def get_floatie(videoID: str, auth: str) -> Response:
    if auth != config["floatie_auth"]:
        return Response(content="Unauthorized", media_type="text/plain", status_code=401)

    proxy = get_proxy_url()
    proxy_url = proxy.url if proxy is not None else None

    try:
        data = fetch_video_data(videoID, proxy_url)

        return Response(content=json.dumps(data), media_type="application/json")
    except Exception as e:
        return Response(content=str(e), media_type="text/plain", status_code=500)

@app.get("/metrics")
def get_metrics() -> Response:
    workers = Worker.all(connection=redis_conn)
    current_time = time.time()
    queues = {"high": queue_high, "low": queue_low}
    queue_gauges = {
        "queue_length": lambda q: len(q),
        "queue_scheduled": lambda q: q.scheduled_job_registry.count,
        "queue_finished": lambda q: q.finished_job_registry.count,
        "queue_failed": lambda q: q.failed_job_registry.count,
        "queue_started": lambda q: q.started_job_registry.count,
        "queue_deferred": lambda q: q.deferred_job_registry.count,
        "queue_cancelled": lambda q: q.canceled_job_registry.count,
    }
    worker_gauges = {
        "current_time": lambda _: current_time,
        "worker_birth_date": lambda w: w.birth_date.timestamp() if w.birth_date else None,
        "worker_busy": lambda w: int(w.get_state() == "busy"),
        "worker_successful_job_count": lambda w: w.successful_job_count,
        "worker_failed_job_count": lambda w: w.failed_job_count,
        "worker_working_time": lambda w: w.total_working_time,
    }

    result = [
        "# HELP dearrow_workers Current amount of connected workers",
        "# TYPE dearrow_workers gauge",
        f"dearrow_workers {len(workers)}",

        "# HELP dearrow_queue_length Current length of the queues",
        "# TYPE dearrow_queue_length gauge",

        "# HELP dearrow_queue_scheduled Current amount of scheduled jobs from the queue",
        "# TYPE dearrow_queue_scheduled gauge",

        "# HELP dearrow_queue_finished Current amount of finished jobs from the queue",
        "# TYPE dearrow_queue_finished gauge",

        "# HELP dearrow_queue_failed Current amount of failed jobs from the queue",
        "# TYPE dearrow_queue_failed gauge",

        "# HELP dearrow_queue_started Current amount of started jobs from the queue",
        "# TYPE dearrow_queue_started gauge",

        "# HELP dearrow_queue_deferred Current amount of deferred jobs from the queue",
        "# TYPE dearrow_queue_deferred gauge",

        "# HELP dearrow_queue_cancelled Current amount of cancelled jobs from the queue",
        "# TYPE dearrow_queue_cancelled gauge",
        *[
            f'dearrow_{g_name}{{queue="{q_name}"}} {func(queue)}'
            for q_name, queue in queues.items()
            for g_name, func in queue_gauges.items()
        ],

        "# HELP dearrow_current_time Current unix time",
        "# TYPE dearrow_current_time gauge",

        "# HELP dearrow_worker_birth_date Unix timestamp at which this worker connected",
        "# TYPE dearrow_worker_birth_date gauge",

        "# HELP dearrow_worker_busy Is this worker busy?",
        "# TYPE dearrow_worker_busy gauge",

        "# HELP dearrow_worker_successful_job_count Number of jobs this worker has successfully completed",
        "# TYPE dearrow_worker_successful_job_count counter",

        "# HELP dearrow_worker_failed_job_count Number of jobs this worker failed to complete",
        "# TYPE dearrow_worker_failed_job_count counter",

        "# HELP dearrow_worker_working_time Number of seconds this worker has spent working",
        "# TYPE dearrow_worker_working_time counter",
        *[
            f'dearrow_{g_name}{{worker_name="{w.name}"}} {result}'
            for w in workers
            for g_name, func in worker_gauges.items()
            if (result := func(w)) is not None
        ],
    ]

    return Response(content="\n".join(result), headers={"Content-Type" : "text/plain; version=0.0.4"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=config["server"]["host"], # type: ignore
                port=config["server"]["port"], reload=config["server"]["reload"],
                log_level="info" if config["debug"] else "warning")
