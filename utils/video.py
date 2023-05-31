from dataclasses import dataclass
import re
from typing import Any, cast
from retry import retry
import yt_dlp # pyright: ignore[reportMissingTypeStubs]
from utils.config import config

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
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL({
        "extractor-args": {
            "youtube": {
                "player_client": "android",
                "skip": "translated_subs,hls,dash"
            },
            "youtube-tab": {
                "skip": "webpage"
            }
        }
    }) as ydl:
        info: Any = ydl.extract_info(url, download=False)

        formats: list[dict[str, str | int]] = ydl.sanitize_info(info)["formats"] # pyright: ignore

        if type(formats) is list:
            formatted_urls = [PlaybackUrl(url["url"], url["width"], url["height"], url["fps"]) 
                for url in cast(list[dict[str, Any]], formats) if "height" in url and url["height"] is not None]
            formatted_urls.reverse()
            formatted_urls.sort(key=lambda url: url.height, reverse=True)

            return formatted_urls
        else:
            raise ValueError("Failed to parse playback URLs: {video_id}")