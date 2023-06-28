from dataclasses import dataclass
import re
from typing import Any, cast
from retry import retry
import yt_dlp # pyright: ignore[reportMissingTypeStubs]
from utils.config import config
import utils.floatie as floatie

@dataclass
class PlaybackUrl:
    url: str
    width: int
    height: int
    fps: int

def valid_video_id(video_id: str) -> bool:
    return type(video_id) is str and re.match(r"^[A-Za-z0-9_\-]{11}$", video_id) is not None

def get_playback_url(video_id: str, height: int = config["default_max_height"]) -> PlaybackUrl:
    playback_urls = get_playback_urls(video_id)

    for url in playback_urls:
        if url.height <= height:
            return url
    
    raise ValueError(f"Failed to find playback URL with height <= {height}")

@retry(tries=3, delay=1, backoff=2)
def get_playback_urls(video_id: str) -> list[PlaybackUrl]:
    formats: list[dict[str, str | int]] | None = None
    errors: list[Exception] = []

    if config["try_floatie"]:
        try:
            formats = floatie.fetch_playback_urls(video_id)
        except Exception as e:
            errors.append(e)
    
    if formats is None:
        # Fallback to ytdlp
        try:
            formats = fetch_playback_urls_from_ytdlp(video_id)
        except Exception as e:
            errors.append(e)

    if formats is None:
        raise ValueError(f"Failed to fetch playback URLs: {video_id} Errors: {','.join([str(error) for error in errors])}") \
            from errors[0]
    
    if any(format_has_av1(format) for format in formats):
        # Filter for only av1
        formats = [format for format in formats if format_has_av1(format)]

    formatted_urls = [PlaybackUrl(url["url"], url["width"], url["height"], url["fps"]) 
        for url in cast(list[dict[str, Any]], formats) if "height" in url and url["height"] is not None]
    
    if formatted_urls[-1].height > 720:
        # Order is the wrong way
        formatted_urls.reverse()
    formatted_urls.sort(key=lambda url: url.height, reverse=True)

    return formatted_urls

def format_has_av1(format: dict[str, str | int]) -> bool:
    return ("mimeType" in format and "av01" in cast(str, format["mimeType"])) \
        or  ("vcodec" in format and "av01" in cast(str, format["vcodec"]))

def fetch_playback_urls_from_ytdlp(video_id: str) -> list[dict[str, str | int]]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL() as ydl:
        info: Any = ydl.extract_info(url, download=False)

        formats: list[dict[str, str | int]] = ydl.sanitize_info(info)["formats"] # pyright: ignore
        if type(formats) is list:
            return formats
        else:
            raise ValueError("Failed to parse playback URLs: {video_id}")