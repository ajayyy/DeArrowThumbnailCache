from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from rq.queue import Queue
from utils.config import config
from utils.redis_handler import redis_conn, wait_for_message

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
                        generateNow: bool = False, title: str | None = None) -> Response:
    if type(videoID) is not str or (type(time) is not float and time is not None) \
            or type(generateNow) is not bool:
        raise HTTPException(status_code=400, detail="Invalid parameters")

    try:
        return handle_thumbnail_response(videoID, time, title, response)
    except FileNotFoundError:
        pass

    if time is None:
        # If we got here with a None time, then there is no thumbnail to pull from
        raise HTTPException(status_code=404, detail="Thumbnail not cached")

    job_id = get_job_id(videoID, time)
    queue = queue_high if generateNow else queue_low

    job = queue.fetch_job(job_id)
    if job is None or job.is_finished:
        # Start the job if it is not already started
        job = queue.enqueue(generate_thumbnail, # pyright: ignore[reportUnknownMemberType]
                        args=(videoID, time, title), job_id=job_id)
    
    result: bool = False
    if generateNow or (job.get_position() or 0 < config["thumbnail_storage"]["max_before_async_generation"]
            and len(queue_high) < config["thumbnail_storage"]["max_before_async_generation"]):
        try:
            result = (await wait_for_message(job_id)) == "true"
        except TimeoutError:
            raise HTTPException(status_code=404, detail="Failed to generate thumbnail due to timeout")
    else:
        raise HTTPException(status_code=404, detail="Thumbnail not generated yet")

    if result:
        return handle_thumbnail_response(videoID, time, title, response)
    else:
        raise HTTPException(status_code=404, detail="Failed to generate thumbnail")
    
def handle_thumbnail_response(video_id: str, time: float | None, title: str | None, response: Response) -> Response:
    thumbnail = get_thumbnail_from_files(video_id, time, title) if time is not None else get_latest_thumbnail_from_files(video_id)
    response.headers["X-Timestamp"] = str(thumbnail.time)
    if thumbnail.title:
        response.headers["X-Title"] = thumbnail.title

    return Response(content=thumbnail.image, media_type="image/webp", headers=response.headers)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=config["server"]["host"], # type: ignore
                port=config["server"]["port"], reload=config["server"]["reload"])