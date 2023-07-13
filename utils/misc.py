import math
from socket import gethostname
import random


def random_hex(length: int) -> str:
    byte_count = math.ceil(length / 2)
    return random.randbytes(byte_count).hex()[:length]


def generate_worker_name() -> str:
    return f"{gethostname()}-{random_hex(4)}"
