from utils.config import config


def log(*data: object) -> None:
    if config["debug"]:
        print(*data)

def log_error(*data: object) -> None:
    print(*data)
