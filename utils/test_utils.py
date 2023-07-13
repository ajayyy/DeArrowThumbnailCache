import sys


def in_test() -> bool:
    return "pytest" in sys.modules
