import yaml
from typing import TypedDict

class ThumbnailStorage(TypedDict):
    path: str
    max_size: int

class Config(TypedDict):
    thumbnail_storage: ThumbnailStorage


config: Config = yaml.safe_load(open("config.yaml"))