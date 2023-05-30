import yaml
from typing import TypedDict

class ServerSettings(TypedDict):
    host: str
    port: int
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
    debug: bool


config: Config = yaml.safe_load(open("config.yaml"))