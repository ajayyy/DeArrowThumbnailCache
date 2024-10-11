from dataclasses import dataclass
import re
from typing import cast
import requests
import json
from utils.config import config

class InnertubeError(Exception):
    pass

class InnertubePlayabilityError(Exception):
    pass

class InnertubeLoginRequiredError(Exception):
    pass

@dataclass
class InnertubeDetails:
    api_key: str
    client_version: str
    client_name: str
    android_version: str

innertube_details = InnertubeDetails(
    api_key="AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w",
    client_version="19.09.36",
    client_name="3",
    android_version="12"
)

context = {
  "client": {
    "clientName": "ANDROID",
    "clientVersion": innertube_details.client_version,
    "androidSdkVersion": 31,
    "osName": "Android",
    "osVersion": innertube_details.android_version,
    "hl": "en",
    "gl": "US",
    "visitorData": config["yt_auth"]["visitorData"]
  }
}

def fetch_playback_urls(video_id: str, proxy_url: str | None) -> list[dict[str, str | int]]:
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    } if proxy_url is not None else None

    if proxy_url:
        print(f"Using proxy {proxy_url}")

    # Get the visitor data token
    url = f"https://www.youtube.com/watch?v={video_id}"

    response = requests.request("GET", url, proxies=proxies, timeout=10)
    if not response.ok:
        raise InnertubeError(f"Google token fetch failed with {response.status_code}")

    visitor_data_match = re.search(r'"VISITOR_DATA":"([^"]+)"', response.text)
    visitor_data = visitor_data_match.group(1) if visitor_data_match else None

    if not visitor_data:
        print("Failed to get visitor data")

    url = f"https://www.youtube.com/youtubei/v1/player?key={innertube_details.api_key}"

    payload = json.dumps({
        "context": context,
        "videoId": video_id,
        "playbackContext": {
            "contentPlaybackContext": {
                "html5Preference": "HTML5_PREF_WANTS"
            }
        },
        "contentCheckOk": True,
        "racyCheckOk": True
    })
    headers = {
        'X-Youtube-Client-Name': innertube_details.client_name,
        'X-Youtube-Client-Version': innertube_details.client_version,
        'X-Goog-Visitor-Id': visitor_data or cast(str, context["client"]["visitorData"]),
        'x-goog-api-format-version': '2',
        'Origin': 'https://www.youtube.com',
        'User-Agent': f'com.google.android.youtube/{innertube_details.client_version} (Linux; U; Android {innertube_details.android_version}; US) gzip',
        'Content-Type': 'application/json',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
        'Sec-Fetch-Mode': 'navigate',
        'Connection': 'close'
    }

    response = requests.request("POST", url, headers=headers, data=payload, proxies=proxies, timeout=10)
    if not response.ok:
        raise InnertubeError(f"Innertube failed with status code {response.status_code}")

    data = response.json()

    playability_status = data["playabilityStatus"]["status"]
    if playability_status != "OK":
        if playability_status == "LOGIN_REQUIRED":
            raise InnertubeLoginRequiredError(f"Login required: {data['playabilityStatus'].get('reason', 'no reason')}")
        else:
            print(data)
            raise InnertubePlayabilityError(f"Not Playable: {data['playabilityStatus']['status']}")

    if data["videoDetails"]["videoId"] != video_id:
        raise InnertubeError(f"Innertube returned wrong video ID: {data['videoDetails']['videoId']} vs. {video_id}")

    return data["streamingData"]["adaptiveFormats"]
