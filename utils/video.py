from dataclasses import dataclass
import re
from typing import Any, cast
import requests
from retry import retry

default_max_height = 720

@dataclass
class PlaybackUrl:
    url: str
    width: int
    height: int
    fps: int

def valid_video_id(video_id: str) -> bool:
    return re.match(r"^[A-Za-z0-9_\-]{11}$", video_id) is not None

def get_playback_url(video_id: str, height: int = default_max_height) -> PlaybackUrl:
    playback_urls = get_playback_urls(video_id)

    for url in playback_urls:
        if url.height <= height:
            return url
    
    raise ValueError(f"Failed to find playback URL with height <= {height}")

@retry(tries=3, delay=1, backoff=2)
def get_playback_urls(video_id: str) -> list[PlaybackUrl]:
    url = "https://www.youtube.com/youtubei/v1/player"
    data = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20230327.07.00"
            }
        },
        "videoId": video_id
    }

    result = requests.post(url, json=data)
    result.raise_for_status()

    playback_urls = result.json()["streamingData"]["adaptiveFormats"]

    if type(playback_urls) is list:
        formatted_urls = [PlaybackUrl(url["url"], url["width"], url["height"], url["fps"]) 
            for url in cast(list[dict[str, Any]], playback_urls) if "width" in url]
        formatted_urls.reverse()
        formatted_urls.sort(key=lambda url: url.height, reverse=True)

        return formatted_urls
    else:
        raise ValueError("Failed to parse playback URLs: {video_id}")