import asyncio
from dataclasses import dataclass
import math
import os
import random
import re
import sys
from typing import Awaitable, Callable, cast
import requests

from .ffmpeg import run_ffmpeg, FFmpegError
import pathlib

from retry import retry
from utils.cleanup import add_storage_used, check_if_cleanup_needed, update_last_used
from utils.proxy import get_proxy_url
from utils.video import PlaybackUrl, get_playback_url, valid_video_id
from utils.nebula import get_nebula_playback_url, valid_nebula_slug, NebulaError
from utils.config import config
import time as time_module
from utils.redis_handler import get_async_redis_conn, redis_conn
from utils.logger import log, log_error
from constants.thumbnail import image_format, metadata_format, minimum_file_size

VIDEO_REQUEST_TIMEOUT = 5

class ThumbnailGenerationError(Exception):
    pass

@dataclass
class Thumbnail:
    image: bytes
    time: float
    title: str | None = None

# Redis queue does not properly support async, and doesn't need it anyway since it is
# only running one job at a time
def generate_thumbnail(video_id: str, time: float, title: str | None, is_livestream: bool = False, update_redis: bool = True) -> None:
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

        generate_and_store_thumbnail(video_id, time, is_livestream)

        _finish_thumbnail_job(
            video_id, time, title, update_redis,
            get_file_paths_fn=lambda vid, t: get_file_paths(vid, t, is_livestream),
            publish_fn=lambda vid, t, s: publish_job_status(vid, t, s),
            label="YouTube",
        )

        log(f"Generated thumbnail for {video_id} at {time} in {time_module.time() - now} seconds")

    except Exception as e:
        log(f"Failed to generate thumbnail for {video_id} at {time}: {e}")
        publish_job_status(video_id, time, "false")
        raise e

@retry(ThumbnailGenerationError, tries=2, delay=1)
def generate_and_store_thumbnail(video_id: str, time: float, is_livestream: bool) -> None:
    print("playback url start", time_module.time())

    proxy = get_proxy_url()
    proxy_url = proxy.url if proxy is not None else None
    try:
        playback_url = get_playback_url(video_id, proxy_url, is_livestream)
    except Exception:
        if proxy is not None and proxy.status_url is not None:
            send_fail_status(proxy.status_url)
        raise

    print("playback url done", time_module.time())

    try:
        try:
            proxy_to_use = proxy_url if config["skip_local_ffmpeg"] else None
            print(f"Generating image for {video_id}, {time_module.time()}"
                    f"{'' if proxy_to_use is None or proxy is None else f' through proxy {proxy.country_code}'}")

            generate_with_ffmpeg(video_id, time, playback_url, is_livestream, proxy_to_use)
            print("generated", time_module.time())
        except FFmpegError:
            if proxy_url is not None and proxy is not None and not config["skip_local_ffmpeg"]:
                # try again through proxy
                print(f"Trying to generate again through the proxy {proxy.country_code} {time_module.time()}")
                generate_with_ffmpeg(video_id, time, playback_url, is_livestream, proxy_url)
            else:
                raise
    except FFmpegError as e:
        if proxy is not None and proxy.status_url is not None:
            send_fail_status(proxy.status_url)

        raise ThumbnailGenerationError \
            (f"Failed to generate thumbnail for {video_id} at {time} with proxy {proxy.country_code if proxy is not None else ''}: {e}")

    if proxy is not None and proxy.status_url is not None:
        send_success_status(proxy.status_url)

def generate_with_ffmpeg(video_id: str, time: float, playback_url: PlaybackUrl,
                            is_livestream: bool, proxy_url: str | None = None) -> None:
    wait_time = 0
    while redis_conn.zcard("concurrent_renders") > config["max_concurrent_renders"]:
        print("Waiting for other renders to finish")
        wait_time += 1

        # Remove expired items every second
        if wait_time % 10 == 0:
            redis_conn.zremrangebyscore("concurrent_renders", "-inf", time_module.time() - 60)

        time_module.sleep(0.1 + 0.05 * random.random())
    redis_conn.zadd("concurrent_renders", { f"{video_id} {time} {is_livestream}": time_module.time() })

    output_folder, output_filename, _, video_filename = get_file_paths(video_id, time, is_livestream)
    pathlib.Path(output_folder).mkdir(parents=True, exist_ok=True)

    # Round down time to nearest frame be consistent with browsers
    rounded_time = int(time * playback_url.fps) / playback_url.fps

    # Rounding error with 60 fps videos cause the wrong frame to render
    if playback_url.fps == 60:
        rounded_time = max(0, rounded_time - 1/100)

    if is_livestream:
        video_request_start = time_module.time()
        def trace_function(*_):
            if time_module.time() - video_request_start > VIDEO_REQUEST_TIMEOUT:
                raise Exception("Video Request Timed Out")

        sys.settrace(trace_function)
        try:
            proxies = {
                "http": proxy_url,
                "https": proxy_url
            } if proxy_url is not None else None

            video = requests.get(playback_url.url,
                                 timeout=5,
                                 proxies=proxies)

            # If this is a manifest file, download the latest video in the manifest
            if video.content.startswith("#EXTM3U".encode()):
                url = video.content.decode().split("\n")[-2]
                if len(url) > 0:
                    video = requests.get(url,
                        timeout=5,
                        proxies=proxies)

            with open(video_filename, "wb") as f:
                f.write(video.content)
                print(f"Downloaded livestream video to {video_filename} with size of {len(video.content)}")
        except Exception:
            try:
                os.remove(video_filename)
            except FileNotFoundError:
                pass

            raise
        finally:
            sys.settrace(None)

    http_proxy = []
    if proxy_url is not None and not is_livestream:
        http_proxy = ["-http_proxy", proxy_url]
    try:
        run_ffmpeg(
            "-y",
            *http_proxy,
            "-ss", str(rounded_time), "-i", video_filename if is_livestream else playback_url.url,
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
    finally:
        if is_livestream:
            os.remove(video_filename)

        redis_conn.zrem("concurrent_renders", f"{video_id} {time} {is_livestream}")

async def get_latest_thumbnail_from_files(video_id: str, is_livestream: bool = False,
                                          *, nebula: bool = False) -> Thumbnail:
    _validate_key(video_id, nebula=nebula)
    return await _get_latest_thumbnail(
        get_folder_path(video_id, nebula=nebula),
        get_best_time(video_id, nebula=nebula),
        lambda t: get_thumbnail_from_files(video_id, t, is_livestream, nebula=nebula),
        video_id,
    )


async def get_thumbnail_from_files(video_id: str, time: float, is_livestream: bool = False,
                                   title: str | None = None,
                                   *, nebula: bool = False) -> Thumbnail:
    _validate_key(video_id, nebula=nebula)
    if type(time) is not float:
        raise ValueError(f"Invalid time: {time}")
    return await _read_thumbnail_files(
        get_folder_path(video_id, nebula=nebula), time, title,
        lambda t: get_file_paths(video_id, t, is_livestream, nebula=nebula),
        get_redis_key(video_id, nebula=nebula),
    )

def get_file_paths(video_id: str, time: float, is_livestream: bool = False,
                   *, nebula: bool = False) -> tuple[str, str, str, str]:
    _validate_key(video_id, nebula=nebula)
    if type(time) is not float:
        raise ValueError(f"Invalid time: {time}")

    output_folder = get_folder_path(video_id, nebula=nebula)
    output_filename = f"{output_folder}/{time}{'-live' if is_livestream else ''}{image_format}"
    metadata_filename = f"{output_folder}/{time}{metadata_format}"
    video_filename = f"{output_folder}/{time}.mp4"

    return (output_folder, output_filename, metadata_filename, video_filename)


def get_folder_path(video_id: str, *, nebula: bool = False) -> str:
    _validate_key(video_id, nebula=nebula)
    base = config['thumbnail_storage']['path']
    return f"{base}/nebula/{video_id}" if nebula else f"{base}/{video_id}"


def get_job_id(video_id: str, time: float, *, nebula: bool = False) -> str:
    return f"nebula_{video_id}-{time}" if nebula else f"{video_id}-{time}"


def get_best_time_key(video_id: str, *, nebula: bool = False) -> str:
    return f"best-nebula:{video_id}" if nebula else f"best-{video_id}"


def get_redis_key(video_id: str, *, nebula: bool = False) -> str:
    return f"nebula:{video_id}" if nebula else video_id


@retry(tries=5, delay=0.1, backoff=3)
def publish_job_status(video_id: str, time: float, status: str,
                      *, nebula: bool = False) -> None:
    redis_conn.publish(get_job_id(video_id, time, nebula=nebula), status)


async def set_best_time(video_id: str, time: float, *, nebula: bool = False) -> None:
    await (await get_async_redis_conn()).set(
        get_best_time_key(video_id, nebula=nebula), time)


async def get_best_time(video_id: str, *, nebula: bool = False) -> bytes | None:
    return cast(bytes | None, await (await get_async_redis_conn()).get(
        get_best_time_key(video_id, nebula=nebula)))


def _validate_key(video_id: str, *, nebula: bool = False) -> None:
    if nebula:
        if not valid_nebula_slug(video_id):
            raise ValueError(f"Invalid Nebula slug: {video_id}")
    else:
        if not valid_video_id(video_id):
            raise ValueError(f"Invalid video ID: {video_id}")

def send_fail_status(proxy_status_url: str) -> None:
    url = proxy_status_url + "api/fail"
    print(f"Sending fail status to {url}")
    try:
        requests.post(url)
    except Exception:
        pass

def send_success_status(proxy_status_url: str) -> None:
    url = proxy_status_url + "api/success"
    print(f"Sending success status to {url}")
    try:
        requests.post(url)
    except Exception:
        pass


# ─── Shared helpers (YouTube + Nebula) ───────────────────────────────────────

def _finish_thumbnail_job(
    video_key: str,
    time: float,
    title: str | None,
    update_redis: bool,
    get_file_paths_fn: Callable[[str, float], tuple[str, str, str, str]],
    publish_fn: Callable[[str, float, str], None],
    label: str = "",
) -> None:
    """Store metadata, track storage, publish success, trigger cleanup.

    Shared by both YouTube and Nebula thumbnail generation jobs.
    *video_key* is the raw ID/slug (not prefixed); *get_file_paths_fn* must
    accept ``(video_key, time)`` and return the standard 4-tuple.
    """
    _, output_filename, metadata_filename, _ = get_file_paths_fn(video_key, time)

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
                log_error(f"Failed to update storage used ({label})", e)
        raise ThumbnailGenerationError(
            f"{label} image for {video_key} at {time} is too small: {image_file_size} bytes")

    if update_redis:
        try:
            asyncio.get_event_loop().run_until_complete(add_storage_used(storage_used))
        except Exception as e:
            log_error(f"Failed to update storage used ({label})", e)

    publish_fn(video_key, time, "true")
    check_if_cleanup_needed()


def _run_ffmpeg_frame_extract(
    render_key: str,
    time: float,
    playback_url: PlaybackUrl,
    file_paths: tuple[str, str, str, str],
    extra_input_args: list[str] | None = None,
) -> None:
    """Wait for concurrency slot, extract one frame with FFmpeg.

    *render_key* is used for the Redis concurrent_renders sorted-set.
    *extra_input_args* allows passing e.g. ``["-http_proxy", url]``.
    """
    wait_time = 0
    while redis_conn.zcard("concurrent_renders") > config["max_concurrent_renders"]:
        print("Waiting for other renders to finish")
        wait_time += 1
        if wait_time % 10 == 0:
            redis_conn.zremrangebyscore("concurrent_renders", "-inf", time_module.time() - 60)
        time_module.sleep(0.1 + 0.05 * random.random())
    redis_conn.zadd("concurrent_renders", {render_key: time_module.time()})

    output_folder, output_filename, _, _ = file_paths
    pathlib.Path(output_folder).mkdir(parents=True, exist_ok=True)

    # Round down time to nearest frame to be consistent with browsers
    rounded_time = int(time * playback_url.fps) / playback_url.fps
    if playback_url.fps == 60:
        rounded_time = max(0, rounded_time - 1 / 100)

    try:
        run_ffmpeg(
            "-y",
            *(extra_input_args or []),
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
    finally:
        redis_conn.zrem("concurrent_renders", render_key)


async def _get_latest_thumbnail(
    output_folder: str,
    best_time_coro: Awaitable[bytes | None],
    get_thumbnail_fn: Callable[[float], Awaitable[Thumbnail]],
    video_key: str,
) -> Thumbnail:
    """Find the most recent thumbnail on disk, preferring *best_time*.

    Shared by YouTube and Nebula retrieval paths.
    """
    try:
        files = os.listdir(output_folder)
    except FileNotFoundError:
        # Ensure the coroutine is consumed even when the folder doesn't exist
        await best_time_coro
        raise FileNotFoundError(f"Failed to find thumbnail for {video_key}")

    files.sort(key=lambda x: os.path.getmtime(os.path.join(output_folder, x)), reverse=True)

    best_time = await best_time_coro
    selected_file: str | None = (
        f"{best_time.decode()}{image_format}" if best_time is not None else None
    )

    if selected_file is None or selected_file not in files:
        selected_file = None
        for file in files:
            if file.endswith(metadata_format):
                selected_file = file
                break
        if selected_file is None:
            for file in files:
                if file.endswith(image_format):
                    selected_file = file
                    break

    if selected_file is not None:
        time = float(re.sub(r"(?:-live)?\.\S{3,4}$", "", selected_file))
        return await get_thumbnail_fn(time)

    raise FileNotFoundError(f"Failed to find thumbnail for {video_key}")


async def _read_thumbnail_files(
    folder_path: str,
    time: float,
    title: str | None,
    get_file_paths_fn: Callable[[float], tuple[str, str, str, str]],
    redis_key: str,
) -> Thumbnail:
    """Read a thumbnail image + metadata from disk.

    Shared by YouTube and Nebula retrieval paths.
    """
    with os.scandir(folder_path) as it:
        truncated_time = math.floor((time * 1000)) / 1000
        truncated_time_string = str(truncated_time)
        if "." in truncated_time_string:
            for entry in it:
                if entry.is_file() and entry.name.endswith(image_format) \
                        and entry.name.startswith(truncated_time_string):
                    try:
                        time = float(entry.name.replace(image_format, ""))
                    except ValueError:
                        continue
                    break

    _, output_filename, metadata_filename, _ = get_file_paths_fn(time)

    with open(output_filename, "rb") as file:
        image_data = file.read()
        if image_data == b"":
            raise FileNotFoundError(
                f"Image for {redis_key} at {time} zero bytes")

        if title is not None:
            with open(metadata_filename, "w") as metadata_file:
                metadata_file.write(title)

        try:
            await update_last_used(redis_key)
        except Exception as e:
            log_error(f"Failed to update last used {e}")

        if title is None and os.path.exists(metadata_filename):
            with open(metadata_filename, "r") as metadata_file:
                return Thumbnail(image_data, time, metadata_file.read())
        else:
            return Thumbnail(image_data, time)


# ─── Nebula thumbnail support ────────────────────────────────────────────────

def generate_nebula_thumbnail(video_slug: str, time: float, title: str | None,
                               update_redis: bool = True) -> None:
    """Generate a thumbnail for a Nebula video via the CF Worker."""
    try:
        now = time_module.time()
        _validate_key(video_slug, nebula=True)
        if type(time) is not float:
            raise ValueError(f"Invalid time: {time}")

        if update_redis:
            try:
                asyncio.get_event_loop().run_until_complete(
                    update_last_used(get_redis_key(video_slug, nebula=True)))
            except Exception as e:
                log_error("Failed to update last used (nebula)", e)

        _generate_and_store_nebula_thumbnail(video_slug, time)

        _finish_thumbnail_job(
            video_slug, time, title, update_redis,
            get_file_paths_fn=lambda vid, t: get_file_paths(vid, t, nebula=True),
            publish_fn=lambda vid, t, s: publish_job_status(vid, t, s, nebula=True),
            label="Nebula",
        )
        log(f"Generated Nebula thumbnail for {video_slug} at {time} "
            f"in {time_module.time() - now} seconds")

    except Exception as e:
        log(f"Failed to generate Nebula thumbnail for {video_slug} at {time}: {e}")
        publish_job_status(video_slug, time, "false", nebula=True)
        raise e


@retry(ThumbnailGenerationError, tries=2, delay=1)
def _generate_and_store_nebula_thumbnail(video_slug: str, time: float) -> None:
    """Fetch Nebula stream URL from the worker and extract a frame with FFmpeg."""
    print("nebula playback url start", time_module.time())

    try:
        playback_url, _ = get_nebula_playback_url(video_slug)
    except NebulaError as e:
        raise ThumbnailGenerationError(
            f"Failed to get Nebula playback URL for {video_slug}: {e}") from e

    print("nebula playback url done", time_module.time())

    try:
        _run_ffmpeg_frame_extract(
            f"nebula:{video_slug}", time, playback_url,
            get_file_paths(video_slug, time, nebula=True),
        )
        print("nebula generated", time_module.time())
    except FFmpegError as e:
        raise ThumbnailGenerationError(
            f"Failed to generate Nebula thumbnail for {video_slug} at {time}: {e}") from e
