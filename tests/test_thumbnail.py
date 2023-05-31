from multiprocessing import Process
import os
import shutil

from fastapi import Response
import pytest
from rq.worker import Worker
from app import get_thumbnail
from utils.redis_handler import reset_async_redis_conn, redis_conn
from utils.thumbnail import generate_thumbnail, get_file_paths

# Clear test cache folder
if os.path.exists("test-cache"):
    shutil.rmtree("test-cache")

@pytest.fixture(scope="function", autouse=True)
def setup():
    reset_async_redis_conn()

@pytest.mark.asyncio
async def test_thumbnail_zero():
    test_video_id = "jNQXAC9IVRw"
    test_time = 0.0
    await load_and_verify_thumbnail(test_video_id, test_time)
    await load_and_verify_request(test_video_id, test_time)

@pytest.mark.asyncio
async def test_thumbnail_non_zero():
    test_video_id = "jNQXAC9IVRw"
    test_time = 5.3
    await load_and_verify_thumbnail(test_video_id, test_time)
    await load_and_verify_request(test_video_id, test_time)

@pytest.mark.asyncio
async def test_thumbnail_with_title():
    test_video_id = "jNQXAC9IVRw"
    test_time = 17.0
    test_title = "Me at the zoo"
    await load_and_verify_thumbnail(test_video_id, test_time, test_title)
    await load_and_verify_request(test_video_id, test_time, test_title)

@pytest.mark.asyncio
async def test_thumbnail_with_title_generate_now():
    worker = Worker("high", connection=redis_conn)
    thread = Process(target=worker.work, args=(worker,))
    thread.start()

    test_video_id = "jNQXAC9IVRw"
    test_time = 15.0
    test_title = "Me at the zoo"
    await load_and_verify_request(test_video_id, test_time, test_title, True, True)

    # Try again without sending the title
    await load_and_verify_request(test_video_id, test_time, test_title)

    thread.kill()

async def load_and_verify_request(video_id: str, time: float, title: str | None = None, send_title: bool = False, generate_now: bool = False) -> None:
    test_response = Response()
    test_result = await get_thumbnail(test_response, video_id, time, generate_now, title if send_title else None)
    assert test_result.status_code == 200
    assert test_result.body != b""
    assert test_result.headers["X-Timestamp"] == str(time)

    if title is not None and not send_title:
        assert test_result.headers["X-Title"] == title

    
async def load_and_verify_thumbnail(video_id: str, time: float, title: str | None = None) -> None:
    generate_thumbnail(video_id, time, title, False)

    # verify file exists
    _, output_filename, metadata_filename = get_file_paths(video_id, time)
    assert os.path.isfile(output_filename)
    with open(output_filename, "rb") as output_file:
        image = output_file.read()
        assert len(image) > 0

    if title is not None:
        assert os.path.isfile(metadata_filename)
        with open(metadata_filename, "r") as metadata_file:
            assert metadata_file.read() == title