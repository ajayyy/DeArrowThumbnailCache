import yaml
from typing import TypedDict

from utils.test_utils import in_test

class ServerSettings(TypedDict):
    host: str
    port: int
    worker_health_check_port: int
    reload: bool

class ThumbnailStorage(TypedDict):
    path: str
    max_size: int
    cleanup_multiplier: float
    redis_offset_allowed: int
    max_before_async_generation: int
    max_queue_size: int

class RedisConfig(TypedDict):
    host: str
    port: int

class ProxyInfoConfig(TypedDict):
    url: str
    country_code: str | None

class YTAuth(TypedDict):
    visitorData: str

class Config(TypedDict):
    server: ServerSettings
    thumbnail_storage: ThumbnailStorage
    redis: RedisConfig
    default_max_height: int
    status_auth_password: str
    yt_auth: YTAuth
    try_floatie: bool
    try_ytdlp: bool
    skip_local_ffmpeg: bool
    proxy_url: str | None
    proxy_urls: list[ProxyInfoConfig] | None
    proxy_token: str | None
    front_auth: str | None
    debug: bool


config: Config = yaml.safe_load(open("config.yaml" if not in_test() else "tests/test_config.yaml"))

if "proxy_url" not in config:
    config["proxy_url"] = None
if "proxy_token" not in config:
    config["proxy_token"] = None
