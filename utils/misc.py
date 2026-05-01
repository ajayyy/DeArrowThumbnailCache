import math
from socket import gethostname
import random

from .config import config


def random_hex(length: int) -> str:
    byte_count = math.ceil(length / 2)
    return random.randbytes(byte_count).hex()[:length]


def generate_worker_name() -> str:
    if config["randomize_worker_names"]:
        return f"{gethostname()}-{random_hex(4)}"
    return gethostname()
