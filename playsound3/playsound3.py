from __future__ import annotations

import atexit
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import urllib.request
from abc import ABC, abstractmethod
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Protocol

from playsound3 import backends

logger = logging.getLogger(__name__)


class PlaysoundException(Exception):
    pass


####################
## DOWNLOAD TOOLS ##
####################

_DOWNLOAD_CACHE: dict[str, str] = {}


def _download_sound_from_web(link: str, destination: Path) -> None:
    # Identifies itself as a browser to avoid HTTP 403 errors
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64)"}
    request = urllib.request.Request(link, headers=headers)

    with urllib.request.urlopen(request) as response, destination.open("wb") as out_file:
        out_file.write(response.read())


def _prepare_path(sound: str | Path) -> str:
    if isinstance(sound, str) and sound.startswith(("http://", "https://")):
        # To play file from URL, we download the file first to a temporary location and cache it
        if sound not in _DOWNLOAD_CACHE:
            sound_suffix = Path(sound).suffix
            with tempfile.NamedTemporaryFile(delete=False, prefix="playsound3-", suffix=sound_suffix) as f:
                _download_sound_from_web(sound, Path(f.name))
                _DOWNLOAD_CACHE[sound] = f.name
        sound = _DOWNLOAD_CACHE[sound]

    path = Path(sound)

    if not path.exists():
        raise PlaysoundException(f"file not found: {sound}")
    return path.absolute().as_posix()


########################
## BACKEND INTERFACES ##
########################


# Imitating subprocess.Popen
class PopenLike(Protocol):
    def poll(self) -> int | None: ...

    def wait(self) -> int: ...

    def terminate(self) -> None: ...


class SoundBackend(ABC):
    """Abstract class for sound backends."""

    @abstractmethod
    def check(self) -> bool:
        raise NotImplementedError("check() must be implemented.")

    @abstractmethod
    def play(self, sound: str) -> PopenLike:
        raise NotImplementedError("play() must be implemented.")


def _set_pdeathsig() -> None:
    """Set the signal delivered to this process if its parent dies."""
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
    except Exception:  # if unavailable (non-Linux) or fails, do nothing
        pass


def get_platform_specific_kwds() -> dict[str, Any]:
    """Get platform-specific keyword arguments for subprocess.Popen."""
    if os.name == "nt":
        return {}
    else:
        # On Unix-like systems, we want to ensure that the child process is terminated if the parent process dies
        return {"preexec_fn": _set_pdeathsig}


def run_as_subprocess(commands: list[str], **kwargs: Any) -> subprocess.Popen[str]:
    """A wrapper around subprocess.Popen to handle platform-specific keyword arguments.

    By default, stdout and stderr are suppressed (set to DEVNULL).
    Additional keyword arguments can be passed and will override defaults.
    """
    popen_kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        **get_platform_specific_kwds(),
        **kwargs,
    }

    return subprocess.Popen(commands, **popen_kwargs)


class Gstreamer(SoundBackend):
    """Gstreamer backend for Linux."""

    def check(self) -> bool:
        try:
            subprocess.run(
                ["gst-play-1.0", "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return True
        except FileNotFoundError:
            return False

    def play(self, sound: str) -> subprocess.Popen[str]:
        return run_as_subprocess(["gst-play-1.0", "--no-interactive", "--quiet", sound])


class Alsa(SoundBackend):
    """ALSA backend for Linux."""

    pty_master: int | None = None

    def check(self) -> bool:
        try:
            subprocess.run(["aplay", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            subprocess.run(["mpg123", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except FileNotFoundError:
            return False

    def play(self, sound: str) -> subprocess.Popen[str]:
        suffix = Path(sound).suffix

        if self.pty_master is None:
            self.pty_master, _ = os.openpty()

        if suffix == ".wav":
            return run_as_subprocess(["aplay", "--quiet", sound])
        elif suffix == ".mp3":
            return run_as_subprocess(["mpg123", "-q", sound], stdin=self.pty_master)
        else:
            raise PlaysoundException(f"ALSA does not support for {suffix} files.")


class Ffplay(SoundBackend):
    """FFplay backend for systems with ffmpeg installed."""

    def check(self) -> bool:
        try:
            subprocess.run(["ffplay", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def play(self, sound: str) -> subprocess.Popen[str]:
        return run_as_subprocess(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", sound])


class Wmplayer(SoundBackend):
    """Windows Media Player backend for Windows."""

    def check(self) -> bool:
        # The recommended way to check for missing library
        if find_spec("pythoncom") is None:
            return False

        try:
            import win32com.client  # type: ignore

            _ = win32com.client.Dispatch("WMPlayer.OCX")
            return True
        except (ImportError, Exception):
            # pywintypes.com_error can be raised, which inherits directly from Exception
            return False

    def play(self, sound: str) -> backends.WmplayerPopen:
        return backends.WmplayerPopen(sound)


class Winmm(SoundBackend):
    """WinMM backend for Windows."""

    def check(self) -> bool:
        try:
            import ctypes

            _ = ctypes.WinDLL("winmm.dll")  # type: ignore
            return True
        except (ImportError, FileNotFoundError, AttributeError):
            return False

    def play(self, sound: str) -> backends.WinmmPopen:
        return backends.WinmmPopen(sound)


class Afplay(SoundBackend):
    """Afplay backend for macOS."""

    def check(self) -> bool:
        # For some reason successful 'afplay -h' returns non-zero code
        # So we must use shutil to test if afplay exists
        return shutil.which("afplay") is not None

    def play(self, sound: str) -> subprocess.Popen[str]:
        return run_as_subprocess(["afplay", sound])


class Appkit(SoundBackend):
    """Appkit backend for macOS."""

    def check(self) -> bool:
        try:
            from AppKit import NSSound  # type: ignore # noqa: F401
            from Foundation import NSURL  # type: ignore # noqa: F401

            return True
        except ImportError:
            return False

    def play(self, sound: str) -> backends.AppkitPopen:
        return backends.AppkitPopen(sound)


################
## PLAYSOUND  ##
################

_NO_BACKEND_MESSAGE = "No supported audio backends on this system!"


def _auto_select_backend() -> str | None:
    if "PLAYSOUND3_BACKEND" in os.environ:
        # Allow users to override the automatic backend choice
        return os.environ["PLAYSOUND3_BACKEND"]

    for backend in _BACKEND_PREFERENCE:
        if backend in AVAILABLE_BACKENDS:
            return backend

    logger.warning(_NO_BACKEND_MESSAGE)
    return None


class Sound:
    """Subprocess-based sound object.

    Attributes:
        backend: The name of the backend used to play the sound.
        subprocess: The subprocess object used to play the sound.
    """

    def __init__(
        self,
        name: str,
        block: bool,
        backend: SoundBackend,
    ) -> None:
        """Initialize the player and begin playing."""
        self.backend: str = str(type(backend)).lower()
        self.subprocess: PopenLike = backend.play(name)

        if block:
            self.wait()

    def is_alive(self) -> bool:
        """Check if the sound is still playing.

        Returns:
            True if the sound is still playing, else False.
        """
        return self.subprocess.poll() is None

    def wait(self) -> None:
        """Block until the sound finishes playing.

        This only makes sense for non-blocking sounds.
        """
        self.subprocess.wait()

    def stop(self) -> None:
        """Stop the sound."""
        self.subprocess.terminate()


def playsound(
    sound: str | Path,
    block: bool = True,
    backend: str | None = None,
) -> Sound:
    """Play a sound file using an available audio backend.

    Args:
        sound: Path or URL of the sound file (string or pathlib.Path).
        block:
            - `True` (default): Wait until sound finishes playing.
            - `False`: Play sound in the background.
        backend: Specific audio backend to use. Leave None for automatic selection.

    Returns:
        Sound object for controlling playback.
    """
    path = _prepare_path(sound)
    backend = backend or DEFAULT_BACKEND
    if backend is None:
        raise PlaysoundException(_NO_BACKEND_MESSAGE)

    if isinstance(backend, str):
        if backend in _BACKEND_MAP:
            backend_obj = _BACKEND_MAP[backend]
        else:
            raise PlaysoundException(f"unknown backend '{backend}'")

    # Unofficially, you can pass a SoundBackend object
    elif isinstance(backend, SoundBackend):
        backend_obj = backend
    elif isinstance(backend, type) and issubclass(backend, SoundBackend):
        backend_obj = backend()
    else:
        raise PlaysoundException(f"invalid backend type '{type(backend)}'")
    return Sound(path, block, backend_obj)


def _remove_cached_downloads(cache: dict[str, str]) -> None:
    """Remove all files saved in the cache when the program ends."""
    for path in cache.values():
        Path(path).unlink()


####################
## INITIALIZATION ##
####################

atexit.register(_remove_cached_downloads, _DOWNLOAD_CACHE)

_BACKEND_PREFERENCE = [
    "gstreamer",  # Linux; should be installed on every distro
    "wmplayer",  # Windows; requires pywin32 -- should be working well on Windows
    "ffplay",  # Multiplatform; requires ffmpeg
    "appkit",  # macOS; requires PyObjC dependency
    "afplay",  # macOS; should be installed on every macOS
    "winmm",  # Windows; should be installed on every Windows, but is quirky with variable bitrate MP3s
    "alsa",  # Linux; only supports .mp3 and .wav and might not be installed
]

_BACKEND_MAP: dict[str, SoundBackend] = {
    name.lower(): obj()
    for name, obj in globals().items()
    if isinstance(obj, type) and issubclass(obj, SoundBackend) and obj is not SoundBackend
}

assert sorted(_BACKEND_PREFERENCE) == sorted(_BACKEND_MAP.keys()), "forgot to update _BACKEND_PREFERENCE?"
AVAILABLE_BACKENDS: list[str] = [name for name in _BACKEND_PREFERENCE if _BACKEND_MAP[name].check()]
DEFAULT_BACKEND: str | None = _auto_select_backend()


# This function is defined here at the bottom because of:
# SyntaxError: annotated name 'DEFAULT_BACKEND' can't be global
def prefer_backends(*backends: str) -> str | None:
    """Add backends to the top of the preference list.

    This function overrides the default backend preference.
    Backend selected here will be used ONLY if available on the system.
    This means this function can be used to update the preference for a
    specific platform without breaking the cross-platform functionality.
    After updating the preferences, the new default backend is returned.

    Args:
        backends: Names of the backends to prefer.

    Returns:
        Name of the newly selected default backend.
    """
    global DEFAULT_BACKEND, _BACKEND_PREFERENCE

    _BACKEND_PREFERENCE = list(backends) + _BACKEND_PREFERENCE
    DEFAULT_BACKEND = _auto_select_backend()
    return DEFAULT_BACKEND
