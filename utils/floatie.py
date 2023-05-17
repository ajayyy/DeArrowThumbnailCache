import requests
import json

IT_CLIENT = {
  API_KEY = AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w,
  CLIENT_VERSION = "17.31.35",
  CLIENT_NAME = "3",
  ANDROID_VERSION = "12"
  context: {
    client: {
      clientName: "ANDROID",
      clientVersion: CLIENT_VERSION,
      androidSdkVersion: 31,
      osName: "Android",
      osVersion: ANDROID_VERSION,
      hl: "en",
      gl: "US"
    }
  }
}

def get_playback_url(video_id: str):
  url = f"https://www.youtube.com/youtubei/v1/player?key={IT_CLIENT.API_KEY}"

  payload = json.dumps({
    "context": IT_CLIENT.context,
    "videoId": video_id,
    "params": "8AEB",
    "playbackContext": {
      "contentPlaybackContext": {
        "html5Preference": "HTML5_PREF_WANTS"
      }
    },
    "contentCheckOk": True,
    "racyCheckOk": True
  })
  headers = {
    'X-Youtube-Client-Name': IT_CLIENT.CLIENT_NAME,
    'X-Youtube-Client-Version': IT_CLIENT.CLIENT_VERSION,
    'Origin': 'https://www.youtube.com',
    'User-Agent': f'com.google.android.youtube/{IT_CLIENT.CLIENT_VERSION} (Linux; U; Android {IT_CLIENT.ANDROID_VERSION}) gzip',
    'Content-Type': 'application/json',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-us,en;q=0.5',
    'Sec-Fetch-Mode': 'navigate',
    'Connection': 'close'
  }

  response = requests.request("POST", url, headers=headers, data=payload)

print(response.text)
