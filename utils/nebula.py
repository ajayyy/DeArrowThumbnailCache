"""
Nebula video support via the Nebula Thumbnail Worker. Hosted on cloudflare workers, maintained by Furdiburd.

The worker holds Nebula credentials and exposes an API that returns
time-limited, JWT-authenticated HLS stream URLs.  This module calls that
API and returns a PlaybackUrl that the rest of the thumbnail pipeline
(FFmpeg frame extraction) can consume unchanged.
"""

import re
from dataclasses import dataclass
from typing import Any

import requests

from utils.config import config
from utils.logger import log
from utils.video import PlaybackUrl


# Nebula video slugs: lowercase alphanumerics, hyphens, underscores.
# e.g. "downieexpress-s2e1", "practical-engineering-rebuilding-the-oroville-dam-spillways"
_SLUG_RE = re.compile(r"^[\w-]+$")

# Default FPS assumption for Nebula HLS streams.
# The worker returns the actual FPS from the manifest when available.
_DEFAULT_FPS = 30


class NebulaError(Exception):
    """Raised when the Nebula worker returns an error."""
    pass


def valid_nebula_slug(slug: str) -> bool:
    """Validate a Nebula video slug."""
    return type(slug) is str and len(slug) > 0 and len(slug) <= 200 \
        and _SLUG_RE.match(slug) is not None


@dataclass
class NebulaVideoInfo:
    """Metadata returned by the Nebula worker alongside the stream URL."""
    title: str
    duration: float
    slug: str


def get_nebula_playback_url(
    video_slug: str,
    height: int = config["default_max_height"],
) -> tuple[PlaybackUrl, NebulaVideoInfo]:
    """
    Call the Nebula Thumbnail Worker to get an authenticated HLS stream URL.

    Returns a (PlaybackUrl, NebulaVideoInfo) tuple.  The PlaybackUrl.url
    points to an HLS variant playlist on starlight.nebula.tv with an
    embedded JWT — FFmpeg can consume it directly.

    Raises NebulaError on any failure.
    """
    worker_url = config.get("nebula_worker_url")  # type: ignore[attr-defined]
    if not worker_url:
        raise NebulaError("nebula_worker_url is not configured")

    if not valid_nebula_slug(video_slug):
        raise ValueError(f"Invalid Nebula video slug: {video_slug}")

    url = f"{worker_url.rstrip('/')}/api/v1/nebula/streamUrl"
    params = {"videoSlug": video_slug, "height": str(height)}
    headers: dict[str, str] = {}

    worker_auth = config.get("nebula_worker_auth_secret")  # type: ignore[attr-defined]
    if worker_auth:
        headers["Authorization"] = f"Bearer {worker_auth}"

    log(f"Fetching Nebula stream URL for {video_slug} at {height}p")

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
    except requests.RequestException as e:
        raise NebulaError(f"Failed to reach Nebula worker: {e}") from e

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", resp.text)
        except Exception:
            detail = resp.text
        raise NebulaError(
            f"Nebula worker returned {resp.status_code}: {detail}"
        )

    try:
        data: dict[str, Any] = resp.json()
    except Exception as e:
        raise NebulaError(f"Invalid JSON from Nebula worker: {e}") from e

    selected = data.get("selectedFormat")
    if not selected or "url" not in selected:
        raise NebulaError("Nebula worker response missing selectedFormat.url")

    playback_url = PlaybackUrl(
        url=selected["url"],
        width=int(selected.get("width", 1280)),
        height=int(selected.get("height", 720)),
        fps=int(selected.get("fps", _DEFAULT_FPS)),
    )

    video_info = NebulaVideoInfo(
        title=data.get("title", video_slug),
        duration=float(data.get("duration", 0)),
        slug=video_slug,
    )

    log(f"Got Nebula stream: {playback_url.width}x{playback_url.height} "
        f"{selected.get('vcodec', '?')} for {video_slug}")

    return playback_url, video_info
