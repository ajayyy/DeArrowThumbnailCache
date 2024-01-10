import os
import shutil
import time
from typing import Tuple

from retry import retry
from utils.config import config
from utils.redis_handler import get_async_redis_conn, redis_conn, queue_high
from constants.thumbnail import image_format

folder_path = config['thumbnail_storage']['path']
max_size = config['thumbnail_storage']['max_size']
target_storage_size = int(max_size * config['thumbnail_storage']['cleanup_multiplier'])
redis_offset_allowed = config["thumbnail_storage"]["redis_offset_allowed"]

def cleanup() -> None:
    # First try cleanup using redis data
    storage_used = int(redis_conn.get(storage_used_key()) or 0)

    if storage_used > target_storage_size:
        cleanup_internal(storage_used)

    before_storage_used = int(redis_conn.get(storage_used_key()) or 0)
    (folder_size, file_count) = get_folder_size(folder_path, True)
    after_storage_used = int(redis_conn.get(storage_used_key()) or 0)

    diff = after_storage_used - before_storage_used

    redis_conn.set(storage_used_key(), folder_size + (diff if diff > 0 else 0))
    redis_conn.set(last_storage_check_key(), int(time.time()))

    if folder_size > target_storage_size:
        storage_saved = cleanup_internal(folder_size, file_count)
        redis_conn.set(storage_used_key(), folder_size - storage_saved)

def cleanup_internal(folder_size: int, file_count: int | None = None) -> int:
    print(f"Storage used: {folder_size} bytes with {file_count} files. Targeting {target_storage_size} bytes.")

    storage_saved = 0
    if folder_size > target_storage_size:
        if file_count is not None and file_count - get_size_of_last_used() > redis_offset_allowed:
            # Need to delete extra video's files
            with os.scandir(folder_path) as it:
                for entry in it:
                    if entry.is_dir() and get_last_used_rank(entry.name) is None:
                        storage_saved += get_folder_size(entry.path)[0]
                        shutil.rmtree(entry.path)
                    if folder_size - storage_saved <= target_storage_size:
                        break

        if folder_size - storage_saved > target_storage_size:
            # Now use redis to find the best options to delete
            while folder_size - storage_saved > target_storage_size:
                video_id = get_oldest_video_id()
                storage_saved += get_folder_size(os.path.join(folder_path, video_id))[0]
                delete_video(video_id)

    return storage_saved

@retry(tries=5, delay=0.1, backoff=3)
def check_if_cleanup_needed() -> None:
    last_storage_check = int(redis_conn.get(last_storage_check_key()) or 0)
    storage_used = int(redis_conn.get(storage_used_key()) or 0)

    # If it has been 30 minutes, call cleanup anyway
    if storage_used > max_size or time.time() - last_storage_check > 30 * 60:
        job_id = get_cleanup_job_id()
        existing_job = queue_high.fetch_job(job_id)

        if existing_job is None or (existing_job.is_failed or existing_job.is_finished
                                    or existing_job.is_canceled or existing_job.is_deferred
                                    or existing_job.is_stopped):
            if existing_job is not None:
                existing_job.delete()
            queue_high.enqueue(cleanup, job_id=job_id, at_front=True, job_timeout="2h")


def get_folder_size(path: str, delete_small_images = False) -> Tuple[int, int]:
    total = 0
    file_count = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file():
                    file_size = entry.stat().st_size
                    if delete_small_images and entry.name.endswith(image_format) and file_size < 200:
                        # Image is probably corrupt
                        os.remove(entry.path)
                    else:
                        total += file_size
                elif entry.is_dir():
                    total += get_folder_size(entry.path, delete_small_images)[0]
                file_count += 1
    except FileNotFoundError:
        pass

    return (total, file_count)

@retry(tries=5, delay=0.1, backoff=3)
def get_oldest_video_id() -> str:
    return redis_conn.zrange(last_used_key(), 0, 0)[0].decode("utf-8")

@retry(tries=5, delay=0.1, backoff=3)
def get_last_used_rank(video_id: str) -> int | None:
    return redis_conn.zrank(last_used_key(), last_used_element_key(video_id))

@retry(tries=5, delay=0.1, backoff=3)
def get_size_of_last_used() -> int:
    return redis_conn.zcard(last_used_key())

@retry(tries=5, delay=0.1, backoff=3)
def delete_video(video_id: str) -> None:
    redis_conn.zrem(last_used_key(), last_used_element_key(video_id))
    try:
        shutil.rmtree(os.path.join(folder_path, video_id))
    except FileNotFoundError:
        print(f"Could not find folder for video {video_id}")

@retry(tries=5, delay=0.1, backoff=3)
async def update_last_used(video_id: str) -> None:
    await (await get_async_redis_conn()).zadd(name=last_used_key(), mapping={
        last_used_element_key(video_id): int(time.time())
    })

@retry(tries=5, delay=0.1, backoff=3)
async def add_storage_used(size: int) -> None:
    await (await get_async_redis_conn()).incrby(storage_used_key(), size)

def last_used_key() -> str:
    return "last-used"

def last_used_element_key(video_id: str) -> str:
    return video_id

def storage_used_key() -> str:
    return "storage-used"

def last_storage_check_key() -> str:
    return "last-storage-check"

def get_cleanup_job_id() -> str:
    return "cleanup"
