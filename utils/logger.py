from utils.config import config


def log(*data: object) -> None:
    if config["debug"]:
        print(*data)