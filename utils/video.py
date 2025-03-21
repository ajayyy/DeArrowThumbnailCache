from dataclasses import dataclass
import re
from typing import Any, cast
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

def get_playback_url(video_id: str, proxy_url: str | None,
                        is_livestream: bool,
                        height: int = config["default_max_height"]) -> PlaybackUrl:
    playback_urls = get_playback_urls(video_id, proxy_url, is_livestream)

    for url in playback_urls:
        if url.height <= height:
            return url

    raise ValueError(f"Failed to find playback URL with height <= {height}")

def get_playback_urls(video_id: str, proxy_url: str | None, is_livestream: bool) -> list[PlaybackUrl]:
    formats: list[dict[str, str | int]] | None = None
    errors: list[Exception] = []

    if config["try_floatie"] or (config["try_floatie_for_live"] and is_livestream):
        try:
            formats = floatie.fetch_playback_urls(video_id, proxy_url)
        except floatie.InnertubePlayabilityError as e:
            print(f"floatie error:{e}")

            # Give up early, let the client generate one since it is geoblocked
            return []
        except Exception as e:
            print(f"floatie error:{e}")
            errors.append(e)

    if formats is None and config["try_ytdlp"]:
        # Fallback to ytdlp
        try:
            formats = fetch_playback_urls_from_ytdlp(video_id, proxy_url)
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

def fetch_playback_urls_from_ytdlp(video_id: str, proxy_url: str | None) -> list[dict[str, str | int]]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL({
        "proxy": proxy_url,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "file_access_retries": 0,
        "socket_timeout": 15,
        "extractor_args": {
            "youtube": {
                "skip": ["dash", "hls", "translated_subs"],
                "player_client": ["tv"]
            }
        }
    }) as ydl:
        info: Any = ydl.extract_info(url, download=False)

        formats: list[dict[str, str | int]] = ydl.sanitize_info(info)["formats"] # pyright: ignore
        if type(formats) is list:
            return formats
        else:
            raise ValueError("Failed to parse playback URLs: {video_id}")
