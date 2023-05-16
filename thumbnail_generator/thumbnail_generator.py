from ffmpeg import FFmpeg # pyright: ignore[reportMissingTypeStubs]
import pathlib
from utils.video import get_playback_url, valid_video_id
from utils.config import config
import time as time_module

def generate_thumbnail(video_id: str, time: float) -> None:
    now = time_module.time()
    if not valid_video_id(video_id):
        raise ValueError(f"Invalid video ID: {video_id}")
    
    playback_url = get_playback_url(video_id)

    # Round down time to nearest frame be consistent with browsers
    time = int(time * playback_url.fps) / playback_url.fps

    output_folder = f"{config['thumbnail_storage']['path']}/{video_id}"
    pathlib.Path(output_folder).mkdir(parents=True, exist_ok=True)

    output_filename = f"{output_folder}/{time}.webp"
    (
        FFmpeg() # pyright: ignore[reportUnknownMemberType]
        .option("y")
        .input(playback_url.url, ss=time)
        .output(output_filename, vframes=1, lossless=0, pix_fmt="bgra")
        .execute()
    )

    print(f"Generated thumbnail for {video_id} at {time} in {time_module.time() - now} seconds")