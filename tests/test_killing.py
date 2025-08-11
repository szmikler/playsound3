import os
import pathlib
import subprocess
import sys
import time
import typing as t

import pytest

from playsound3 import AVAILABLE_BACKENDS
from playsound3.playsound3 import _prepare_path

loc_mp3_3s = "tests/sounds/sample3s.mp3"
loc_flc_3s = "tests/sounds/sample3s.flac"
web_wav_3s = "https://samplelib.com/lib/preview/wav/sample-3s.wav"

# Download web files to the local cache
for url in [web_wav_3s]:
    _prepare_path(url)


def get_supported_sounds(backend: str):
    not_supporting_flac = ["alsa", "winmm"]
    if backend in not_supporting_flac:
        return [loc_mp3_3s, web_wav_3s]
    else:
        return [loc_mp3_3s, loc_flc_3s, web_wav_3s]


def _iter_pids() -> t.Iterable[int]:
    proc = pathlib.Path("/proc")
    for p in proc.iterdir():
        if p.name.isdigit():
            yield int(p.name)


def _read_file(path: pathlib.Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception:
        return b""


def list_tagged_player_pids(tag: str) -> t.List[int]:
    """Return PIDs whose environ contains TAG=<tag>"""
    pids = []
    for pid in _iter_pids():
        base = pathlib.Path(f"/proc/{pid}")
        env = _read_file(base / "environ")
        if not env:
            continue
        # /proc/<pid>/environ is NUL-separated key=val entries
        if f"PLAYSOUND_TEST_TAG={tag}".encode() not in env.split(b"\x00"):
            continue
        cmdline = _read_file(base / "cmdline").replace(b"\x00", b" ").lower()
        if not cmdline:
            continue
        pids.append(pid)
    return pids


HELPER_CODE = """
import os, sys, time
from playsound3 import playsound

sound = playsound({path!r}, block=False, backend={backend!r})
time.sleep(10)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: relies on /proc and PDEATHSIG semantics")
def test_killing_parent():
    TAG = "__test_killing_tag"

    for backend in AVAILABLE_BACKENDS:
        for path in get_supported_sounds(backend):
            assert len(list_tagged_player_pids(TAG)) == 0
            code = HELPER_CODE.format(path=path, backend=backend)

            environ = os.environ.copy()
            environ["PLAYSOUND_TEST_TAG"] = TAG
            proc = subprocess.Popen(["python", "-c", code], env=environ)

            time.sleep(2.5)
            assert len(list_tagged_player_pids(TAG)) == 2

            proc.kill()
            assert len(list_tagged_player_pids(TAG)) == 0
