from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from rq.queue import Queue
from utils.config import config
from utils.redis_handler import redis_conn, wait_for_message
from utils.logger import log

from utils.thumbnail import generate_thumbnail, get_latest_thumbnail_from_files, get_job_id, get_thumbnail_from_files

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Timestamp", "X-Title"]
)

queue_high = Queue("high", connection=redis_conn)
queue_low = Queue("default", connection=redis_conn)

@app.get("/api/v1/getThumbnail")
async def get_thumbnail(response: Response, videoID: str, time: float | None = None,
                        generateNow: bool = False, title: str | None = None,
                        officialTime: bool = False) -> Response:
    if type(videoID) is not str or (type(time) is not float and time is not None) \
            or type(generateNow) is not bool:
        raise HTTPException(status_code=400, detail="Invalid parameters")

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
            queue_low.remove(other_queue_job) # pyright: ignore[reportUnknownMemberType]
        elif job is not None:
            # New queue is low, old queue is high, prefer old one
            queue.remove(job) # pyright: ignore[reportUnknownMemberType]
            job = other_queue_job
        else:
            # New queue is low, old queue is high, prefer old one
            job = other_queue_job


    if job is None or job.is_finished or job.is_failed:
        # Start the job if it is not already started
        job = queue.enqueue(generate_thumbnail, # pyright: ignore[reportUnknownMemberType]
                        args=(videoID, time, officialTime, title), job_id=job_id)
    
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
    thumbnail = get_thumbnail_from_files(video_id, time, title) if time is not None else await get_latest_thumbnail_from_files(video_id)
    response.headers["X-Timestamp"] = str(thumbnail.time)
    if thumbnail.title:
        response.headers["X-Title"] = thumbnail.title.strip()

    return Response(content=thumbnail.image, media_type="image/webp", headers=response.headers)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=config["server"]["host"], # type: ignore
                port=config["server"]["port"], reload=config["server"]["reload"])