from dataclasses import dataclass
import os
import re
from ffmpeg import FFmpeg # pyright: ignore[reportMissingTypeStubs]
import pathlib
from utils.video import get_playback_url, valid_video_id
from utils.config import config
import time as time_module
from utils.redis_handler import get_async_redis_conn, redis_conn

image_format: str = ".webp"
metadata_format: str = ".txt"

@dataclass
class Thumbnail:
    image: bytes
    time: float
    title: str | None = None

def generate_thumbnail(video_id: str, time: float, officialTime: bool, title: str | None) -> None:
    try:
        now = time_module.time()
        if not valid_video_id(video_id):
            raise ValueError(f"Invalid video ID: {video_id}")
        if type(time) is not float:
            raise ValueError(f"Invalid time: {time}")

        playback_url = get_playback_url(video_id)

        # Round down time to nearest frame be consistent with browsers
        rounded_time = int(time * playback_url.fps) / playback_url.fps

        output_folder, output_filename, metadata_filename = get_file_paths(video_id, time)
        pathlib.Path(output_folder).mkdir(parents=True, exist_ok=True)

        (
            FFmpeg() # pyright: ignore[reportUnknownMemberType]
            .option("y")
            .input(playback_url.url, ss=rounded_time)
            .output(output_filename, vframes=1, lossless=0, pix_fmt="bgra")
            .execute()
        )

        if title:
            with open(metadata_filename, "w") as metadata_file:
                metadata_file.write(title)

        redis_conn.publish(get_job_id(video_id, time), "true")
        print(f"Generated thumbnail for {video_id} at {time} in {time_module.time() - now} seconds")

        if officialTime:
            redis_conn.set(get_best_time_key(video_id), time)

    except Exception as e:
        print(f"Failed to generate thumbnail for {video_id} at {time}: {e}")
        redis_conn.publish(get_job_id(video_id, time), "false")
        raise e

async def get_latest_thumbnail_from_files(video_id: str) -> Thumbnail:
    if not valid_video_id(video_id):
        raise ValueError(f"Invalid video ID: {video_id}")

    output_folder = get_folder_path(video_id)

    files = os.listdir(output_folder)
    files.sort(key=lambda x: os.path.getmtime(os.path.join(output_folder, x)), reverse=True)

    best_time = await (await get_async_redis_conn()).get(get_best_time_key(video_id))

    selected_file: str | None = f"{best_time}{image_format}" if best_time else None
    
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
        return get_thumbnail_from_files(video_id, time)
        
    raise FileNotFoundError(f"Failed to find thumbnail for {video_id}")

def get_thumbnail_from_files(video_id: str, time: float, title: str | None = None) -> Thumbnail:
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