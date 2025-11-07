__license__ = "MIT"
__version__ = "3.3.0"
__author__ = "Szymon Mikler"

from playsound3.playsound3 import (
    AVAILABLE_BACKENDS,
    DEFAULT_BACKEND,
    PlaysoundException,
    playsound,
    prefer_backends,
)

__all__ = [
    "AVAILABLE_BACKENDS",
    "DEFAULT_BACKEND",
    "playsound",
    "prefer_backends",
    "PlaysoundException",
]
