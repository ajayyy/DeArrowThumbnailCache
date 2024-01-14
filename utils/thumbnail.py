import asyncio
from dataclasses import dataclass
import os
import re
from typing import cast
from .ffmpeg import run_ffmpeg, FFmpegError
import pathlib

from retry import retry
from utils.cleanup import add_storage_used, check_if_cleanup_needed, update_last_used
from utils.proxy import get_proxy_url
from utils.video import PlaybackUrl, get_playback_url, valid_video_id
from utils.config import config
import time as time_module
from utils.redis_handler import get_async_redis_conn, redis_conn
from utils.logger import log, log_error
from constants.thumbnail import image_format, metadata_format, minimum_file_size

class ThumbnailGenerationError(Exception):
    pass

@dataclass
class Thumbnail:
    image: bytes
    time: float
    title: str | None = None

# Redis queue does not properly support async, and doesn't need it anyway since it is
# only running one job at a time
def generate_thumbnail(video_id: str, time: float, title: str | None, update_redis: bool = True) -> None:
    try:
        now = time_module.time()
        if not valid_video_id(video_id):
            raise ValueError(f"Invalid video ID: {video_id}")
        if type(time) is not float:
            raise ValueError(f"Invalid time: {time}")

        if update_redis:
            try:
                asyncio.get_event_loop().run_until_complete(update_last_used(video_id))
            except Exception as e:
                log_error("Failed to update last used", e)

        generate_and_store_thumbnail(video_id, time)

        _, output_filename, metadata_filename = get_file_paths(video_id, time)
        if title is not None:
            with open(metadata_filename, "w") as metadata_file:
                metadata_file.write(title)

        title_file_size = len(title.encode("utf-8")) if title else 0
        image_file_size = os.path.getsize(output_filename)
        storage_used = title_file_size + image_file_size

        if image_file_size < minimum_file_size:
            os.remove(output_filename)
            if update_redis:
                try:
                    asyncio.get_event_loop().run_until_complete(add_storage_used(title_file_size))
                except Exception as e:
                    log_error("Failed to update storage used", e)

            raise ThumbnailGenerationError(f"Image file for {video_id} at {time} is too small, probably a premiere: {image_file_size} bytes")

        if update_redis:
            try:
                asyncio.get_event_loop().run_until_complete(add_storage_used(storage_used))
            except Exception as e:
                log_error("Failed to update storage used", e)
        publish_job_status(video_id, time, "true")
        check_if_cleanup_needed()

        log(f"Generated thumbnail for {video_id} at {time} in {time_module.time() - now} seconds")

    except Exception as e:
        log(f"Failed to generate thumbnail for {video_id} at {time}: {e}")
        publish_job_status(video_id, time, "false")
        raise e

@retry(ThumbnailGenerationError, tries=2, delay=1)
def generate_and_store_thumbnail(video_id: str, time: float) -> None:
    print("playback url start", time_module.time())

    proxy = get_proxy_url()
    proxy_url = proxy.url if proxy is not None else None
    playback_url = get_playback_url(video_id, proxy_url)

    print("playback url done", time_module.time())

    try:
        try:
            proxy_to_use = proxy_url if config["skip_local_ffmpeg"] else None
            print(f"Generating image for {video_id}, {time_module.time()}"
                    f"{'' if proxy_to_use is None or proxy is None else f' through proxy {proxy.country_code}'}")

            generate_with_ffmpeg(video_id, time, playback_url, proxy_to_use)
            print("generated", time_module.time())
        except FFmpegError:
            if proxy_url is not None and proxy is not None:
                # try again through proxy
                print(f"Trying to generate again through the proxy {proxy.country_code} {time_module.time()}")
                generate_with_ffmpeg(video_id, time, playback_url, proxy_url)
            else:
                raise
    except FFmpegError as e:
        raise ThumbnailGenerationError \
            (f"Failed to generate thumbnail for {video_id} at {time} with proxy {proxy.country_code if proxy is not None else ''}: {e}")

def generate_with_ffmpeg(video_id: str, time: float, playback_url: PlaybackUrl,
                            proxy_url: str | None = None) -> None:
    output_folder, output_filename, _ = get_file_paths(video_id, time)
    pathlib.Path(output_folder).mkdir(parents=True, exist_ok=True)

    # Round down time to nearest frame be consistent with browsers
    rounded_time = int(time * playback_url.fps) / playback_url.fps

    http_proxy = []
    if proxy_url is not None:
        http_proxy = ["-http_proxy", proxy_url]
    try:
        run_ffmpeg(
            "-y",
            *http_proxy,
            "-ss", str(rounded_time), "-i", playback_url.url,
            "-vframes", "1", "-lossless", "0", "-pix_fmt", "bgra", output_filename,
            "-timelimit", "20",
            timeout=20,
        )
    except FFmpegError:
        try:
            os.remove(output_filename)
        except FileNotFoundError:
            pass

        raise

async def get_latest_thumbnail_from_files(video_id: str) -> Thumbnail:
    if not valid_video_id(video_id):
        raise ValueError(f"Invalid video ID: {video_id}")

    output_folder = get_folder_path(video_id)

    files = os.listdir(output_folder)
    files.sort(key=lambda x: os.path.getmtime(os.path.join(output_folder, x)), reverse=True)

    best_time = await get_best_time(video_id)

    selected_file: str | None = f"{best_time.decode()}{image_format}" if best_time is not None else None

    # Fallback to latest image
    if selected_file is None or selected_file not in files:
        selected_file = None

        for file in files:
            # First try latest metadata file
            # Most recent with a title is probably best
            if file.endswith(metadata_format):
                selected_file = file
                break

        if selected_file is None:
            # Fallback to latest image
            for file in files:
                if file.endswith(image_format):
                    selected_file = file
                    break

    if selected_file is not None:
        # Remove file extension
        time = float(re.sub(r"\.\S{3,4}$", "", selected_file))
        return await get_thumbnail_from_files(video_id, time)

    raise FileNotFoundError(f"Failed to find thumbnail for {video_id}")

async def get_thumbnail_from_files(video_id: str, time: float, title: str | None = None) -> Thumbnail:
    if not valid_video_id(video_id):
        raise ValueError(f"Invalid video ID: {video_id}")
    if type(time) is not float:
        raise ValueError(f"Invalid time: {time}")

    _, output_filename, metadata_filename = get_file_paths(video_id, time)

    with open(output_filename, "rb") as file:
        image_data = file.read()
        if image_data == b"":
            raise FileNotFoundError(f"Image file for {video_id} at {time} zero bytes")

        if title is not None:
            with open(metadata_filename, "w") as metadata_file:
                metadata_file.write(title)

        try:
            await update_last_used(video_id)
        except Exception as e:
            log_error(f"Failed to update last used {e}")

        if title is None and os.path.exists(metadata_filename):
            with open(metadata_filename, "r") as metadata_file:
                return Thumbnail(image_data, time, metadata_file.read())
        else:
            return Thumbnail(image_data, time)

def get_file_paths(video_id: str, time: float) -> tuple[str, str, str]:
    if not valid_video_id(video_id):
        raise ValueError(f"Invalid video ID: {video_id}")
    if type(time) is not float:
        raise ValueError(f"Invalid time: {time}")


    output_folder = get_folder_path(video_id)
    output_filename = f"{output_folder}/{time}{image_format}"
    metadata_filename = f"{output_folder}/{time}{metadata_format}"

    return (output_folder, output_filename, metadata_filename)

def get_folder_path(video_id: str) -> str:
    if not valid_video_id(video_id):
        raise ValueError(f"Invalid video ID: {video_id}")

    return f"{config['thumbnail_storage']['path']}/{video_id}"

def get_job_id(video_id: str, time: float) -> str:
    return f"{video_id}-{time}"

def get_best_time_key(video_id: str) -> str:
    return f"best-{video_id}"

@retry(tries=5, delay=0.1, backoff=3)
def publish_job_status(video_id: str, time: float, status: str) -> None:
    redis_conn.publish(get_job_id(video_id, time), status)

async def set_best_time(video_id: str, time: float) -> None:
    await (await get_async_redis_conn()).set(get_best_time_key(video_id), time)

async def get_best_time(video_id: str) -> bytes | None:
    return cast(bytes | None, await (await get_async_redis_conn()).get(get_best_time_key(video_id)))
