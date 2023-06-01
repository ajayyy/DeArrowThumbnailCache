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
    redis_offset_allowed: int
    max_before_async_generation: int

class RedisConfig(TypedDict):
    host: str
    port: int

class Config(TypedDict):
    server: ServerSettings
    thumbnail_storage: ThumbnailStorage
    redis: RedisConfig
    default_max_height: int
    status_auth_password: str
    debug: bool


config: Config = yaml.safe_load(open("config.yaml" if not in_test() else "tests/test_config.yaml"))