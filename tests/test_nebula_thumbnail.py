"""
Test for Nebula thumbnail generation using yt-dlp with authentication.

Nebula requires authentication to access video streams. This test verifies
that yt-dlp can authenticate with Nebula and that a thumbnail frame can be
extracted using FFmpeg.

Usage:

    # API token from browser DevTools (Application > Cookies > nebula_auth.apiToken)
        export NEBULA_API_TOKEN="<your token>"

    # Or: Netscape-format cookies file
        export NEBULA_COOKIES_FILE="/path/to/cookies.txt"

    # Optional: save the thumbnail to a fixed path for visual inspection
        export NEBULA_SAVE_PATH="/tmp/nebula_test_thumb.webp"

    Then run:
        pytest tests/test_nebula_thumbnail.py -v -s
"""

import os
import http.cookiejar
import shutil
import tempfile

import pytest
import yt_dlp  # pyright: ignore[reportMissingTypeStubs]
from typing import Any, cast

from utils.ffmpeg import run_ffmpeg
from constants.thumbnail import image_format

NEBULA_VIDEO_URL = "https://nebula.tv/videos/downieexpress-s2e1"
NEBULA_TIMESTAMP = 60.0  # 1:00

NEBULA_API_TOKEN = os.environ.get("NEBULA_API_TOKEN")
NEBULA_COOKIES_FILE = os.environ.get("NEBULA_COOKIES_FILE")

# If set, the end-to-end test will also copy the thumbnail to the save path for visual inspection
NEBULA_SAVE_PATH = os.environ.get("NEBULA_SAVE_PATH")

has_credentials = bool(NEBULA_API_TOKEN or NEBULA_COOKIES_FILE)

skip_reason = (
    "Nebula credentials not provided. "
    "Set NEBULA_API_TOKEN (value of the nebula_auth.apiToken browser cookie) "
    "or NEBULA_COOKIES_FILE to a Netscape-format cookies file."
)


def create_nebula_ytdlp(cookies_file: str | None = None) -> yt_dlp.YoutubeDL:
    """Create a yt-dlp instance configured for Nebula authentication.

    Auth priority: API token cookie > cookies file.
    """
    opts: dict[str, object] = {
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 2,
        "socket_timeout": 30,
        "quiet": False,
        "no_warnings": False,
    }

    if cookies_file:
        opts["cookiefile"] = cookies_file

    ydl = yt_dlp.YoutubeDL(opts)  # pyright: ignore[reportArgumentType]

    if NEBULA_API_TOKEN:
        # Inject the API token as a cookie so yt-dlp's Nebula extractor
        # picks it up during _real_initialize().
        cookie = http.cookiejar.Cookie(
            version=0,
            name="nebula_auth.apiToken",
            value=NEBULA_API_TOKEN,
            port=None,
            port_specified=False,
            domain="nebula.tv",
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
        ydl.cookiejar.set_cookie(cookie)

    return ydl


@pytest.mark.skipif(not has_credentials, reason=skip_reason)
class TestNebulaThumbnail:
    """Tests for Nebula video thumbnail extraction via yt-dlp + FFmpeg."""

    output_dir: str

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Create and clean up a temporary output directory."""
        self.output_dir = tempfile.mkdtemp(prefix="nebula_thumb_test_")
        yield
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_ytdlp_can_extract_nebula_info(self):
        """Test that yt-dlp can authenticate and extract video info from Nebula."""
        ydl = create_nebula_ytdlp(NEBULA_COOKIES_FILE)

        info = ydl.extract_info(NEBULA_VIDEO_URL, download=False)
        assert info is not None

        sanitized = cast(dict[str, Any], ydl.sanitize_info(info))
        print(f"\nVideo title: {sanitized.get('title')}")
        print(f"Video duration: {sanitized.get('duration')}s")
        print(f"Video ID: {sanitized.get('id')}")
        print(f"Uploader: {sanitized.get('uploader')}")

        formats: list[dict[str, Any]] = sanitized.get("formats", [])
        assert len(formats) > 0, "No formats found — authentication may have failed"

        # Filter to video-only formats (with height info)
        video_formats = [f for f in formats if f.get("height") is not None and f.get("height", 0) > 0]
        assert len(video_formats) > 0, "No video formats with height info found"

        print(f"\nAvailable video formats ({len(video_formats)}):")
        for fmt in video_formats:
            print(f"  {fmt.get('format_id')}: {fmt.get('width')}x{fmt.get('height')} "
                  f"fps={fmt.get('fps')} vcodec={fmt.get('vcodec')} "
                  f"ext={fmt.get('ext')}")

        # Verify duration is long enough for test timestamp
        duration = cast(int, sanitized.get("duration", 0))
        assert duration >= NEBULA_TIMESTAMP, (
            f"Video duration ({duration}s) is shorter than test timestamp ({NEBULA_TIMESTAMP}s)"
        )

    def test_ytdlp_can_get_playback_url(self):
        """Test that a usable playback URL can be obtained for a Nebula video."""
        ydl = create_nebula_ytdlp(NEBULA_COOKIES_FILE)

        info = ydl.extract_info(NEBULA_VIDEO_URL, download=False)
        assert info is not None
        sanitized = cast(dict[str, Any], ydl.sanitize_info(info))

        formats: list[dict[str, Any]] = sanitized.get("formats", [])
        video_formats = [
            f for f in formats
            if f.get("height") is not None
            and f.get("height", 0) > 0
            and f.get("url")
        ]
        assert len(video_formats) > 0, "No video formats with URLs found"

        # Pick a format with height <= 720
        video_formats.sort(key=lambda f: f.get("height", 0), reverse=True)
        selected = None
        for fmt in video_formats:
            if fmt.get("height", 9999) <= 720:
                selected = fmt
                break

        if selected is None:
            # Fallback: just pick the smallest available
            selected = video_formats[-1]

        print(f"\nSelected format: {selected.get('format_id')} "
              f"{selected.get('width')}x{selected.get('height')} "
              f"fps={selected.get('fps')} vcodec={selected.get('vcodec')}")
        print(f"URL length: {len(selected.get('url', ''))}")

        assert selected.get("url"), "Selected format has no URL"
        assert selected.get("height", 0) > 0, "Selected format has no height"

    def test_generate_nebula_thumbnail(self):
        """End-to-end: authenticate, get stream URL, extract thumbnail at 1:00 with FFmpeg."""
        ydl = create_nebula_ytdlp(NEBULA_COOKIES_FILE)

        # Step 1: Extract info
        print(f"\n[1/3] Extracting video info from {NEBULA_VIDEO_URL}...")
        info = ydl.extract_info(NEBULA_VIDEO_URL, download=False)
        assert info is not None
        sanitized = cast(dict[str, Any], ydl.sanitize_info(info))

        title = sanitized.get("title", "Unknown")
        print(f"  Title: {title}")

        # Step 2: Pick a suitable format
        formats: list[dict[str, Any]] = sanitized.get("formats", [])
        video_formats = [
            f for f in formats
            if f.get("height") is not None
            and f.get("height", 0) > 0
            and f.get("url")
        ]
        assert len(video_formats) > 0, "No usable video formats found"

        video_formats.sort(key=lambda f: f.get("height", 0), reverse=True)
        selected = None
        for fmt in video_formats:
            if fmt.get("height", 9999) <= 720:
                selected = fmt
                break
        if selected is None:
            selected = video_formats[-1]

        playback_url = selected["url"]
        height = selected.get("height", 0)
        width = selected.get("width", 0)
        fps = selected.get("fps", 30)

        print(f"[2/3] Selected format: {width}x{height} @ {fps}fps")

        # Step 3: Extract thumbnail with FFmpeg
        output_filename = os.path.join(self.output_dir, f"{NEBULA_TIMESTAMP}{image_format}")

        # Round time to nearest frame (same logic as the main code)
        rounded_time = int(NEBULA_TIMESTAMP * fps) / fps

        print(f"[3/3] Extracting thumbnail at {NEBULA_TIMESTAMP}s (rounded: {rounded_time}s)...")

        run_ffmpeg(
            "-y",
            "-ss", str(rounded_time),
            "-i", playback_url,
            "-vframes", "1",
            "-lossless", "0",
            "-pix_fmt", "bgra",
            output_filename,
            "-timelimit", "30",
            timeout=30,
        )

        # Verify the thumbnail was generated
        assert os.path.isfile(output_filename), f"Thumbnail file was not created at {output_filename}"

        file_size = os.path.getsize(output_filename)
        print(f"\n  ✓ Thumbnail generated successfully!")
        print(f"  File: {output_filename}")
        print(f"  Size: {file_size} bytes")

        assert file_size > 0, "Thumbnail file is empty"
        assert file_size > 100, f"Thumbnail file is suspiciously small ({file_size} bytes)"

        # Read and verify it looks like a valid image
        with open(output_filename, "rb") as f:
            header = f.read(16)
            # WebP files start with "RIFF" and contain "WEBP"
            if image_format == ".webp":
                assert header[:4] == b"RIFF", f"Output doesn't look like a WebP file (header: {header[:4]})"
                assert header[8:12] == b"WEBP", f"Output doesn't look like a WebP file"
            print(f"  Format verified: {image_format}")

        # If NEBULA_SAVE_PATH is set, copy to a persistent location for visual inspection
        if NEBULA_SAVE_PATH:
            shutil.copy2(output_filename, NEBULA_SAVE_PATH)
            print(f"  Saved for visual inspection: {NEBULA_SAVE_PATH}")

    def test_generate_nebula_thumbnail_low_res(self):
        """
        Test thumbnail generation with the lowest available resolution
        to minimize bandwidth during testing.
        """
        ydl = create_nebula_ytdlp(NEBULA_COOKIES_FILE)

        info = ydl.extract_info(NEBULA_VIDEO_URL, download=False)
        assert info is not None
        sanitized = cast(dict[str, Any], ydl.sanitize_info(info))

        formats: list[dict[str, Any]] = sanitized.get("formats", [])
        video_formats = [
            f for f in formats
            if f.get("height") is not None
            and f.get("height", 0) > 0
            and f.get("url")
        ]
        assert len(video_formats) > 0

        # Pick the lowest resolution
        video_formats.sort(key=lambda f: f.get("height", 0))
        selected = video_formats[0]

        playback_url = selected["url"]
        fps = selected.get("fps", 30)
        rounded_time = int(NEBULA_TIMESTAMP * fps) / fps

        output_filename = os.path.join(self.output_dir, f"lowres_{NEBULA_TIMESTAMP}{image_format}")

        print(f"\nGenerating low-res thumbnail at {selected.get('width')}x{selected.get('height')}...")

        run_ffmpeg(
            "-y",
            "-ss", str(rounded_time),
            "-i", playback_url,
            "-vframes", "1",
            "-lossless", "0",
            "-pix_fmt", "bgra",
            output_filename,
            "-timelimit", "30",
            timeout=30,
        )

        assert os.path.isfile(output_filename)
        file_size = os.path.getsize(output_filename)
        print(f"  ✓ Low-res thumbnail: {file_size} bytes")
        assert file_size > 100
