"""Thin ffmpeg/ffprobe wrappers shared by the engine and the UI."""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class MediaError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    return _tool("ffmpeg")


def ffprobe_bin() -> str:
    return _tool("ffprobe")


def _android_native_dir() -> Path | None:
    """Directory holding the app's native libraries on Android."""
    try:
        from jnius import autoclass

        info = autoclass("org.kivy.android.PythonActivity").mActivity.getApplicationInfo()
        return Path(str(info.nativeLibraryDir))
    except Exception:
        return None


def _tool(name: str) -> str:
    override = os.environ.get(f"AUTOCLIP_{name.upper()}")
    if override:
        return override
    if "ANDROID_ARGUMENT" in os.environ:
        # python-for-android installs the ffmpeg CLI as libffmpegbin.so so it
        # lands in jniLibs and stays executable; there is no ffprobe.
        native = _android_native_dir()
        if native is not None:
            candidate = native / f"lib{name}bin.so"
            if candidate.exists():
                return str(candidate)
    # A packaged build ships its own ffmpeg; use it so the app never depends on
    # whatever happens to be on the user's PATH.
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        bundled = Path(frozen) / "bin" / (f"{name}.exe" if os.name == "nt" else name)
        if bundled.exists():
            if os.name != "nt":
                bundled.chmod(0o755)
            return str(bundled)
    found = shutil.which(name)
    if found:
        return found
    bundled = Path(__file__).resolve().parent / "bin" / (f"{name}.exe" if os.name == "nt" else name)
    if bundled.exists():
        return str(bundled)
    raise MediaError(
        f"{name} not found. Install ffmpeg or set AUTOCLIP_{name.upper()} to its path."
    )


@functools.lru_cache(maxsize=1)
def available_filters() -> frozenset[str]:
    """Filter names this ffmpeg build knows about.

    python-for-android ships a stripped build (crop/scale/volume only), so the
    renderer asks instead of assuming.
    """
    try:
        proc = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-nostdin", "-filters"],
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0][0] in "TSC.":
            names.add(parts[1])
    return frozenset(names)


def has_filter(name: str) -> bool:
    return name in available_filters()


def have_ffmpeg() -> bool:
    try:
        ffmpeg_bin()
        return True
    except MediaError:
        return False


def run(args: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-6:]
        raise MediaError("ffmpeg failed: " + " | ".join(tail))
    return proc


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    has_video: bool
    has_audio: bool


def have_ffprobe() -> bool:
    try:
        ffprobe_bin()
        return True
    except MediaError:
        return False


def probe(path: str | Path) -> MediaInfo:
    if not have_ffprobe():
        return _probe_with_ffmpeg(path)
    return _probe_with_ffprobe(path)


_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_SIZE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")


def _probe_with_ffmpeg(path: str | Path) -> MediaInfo:
    """Fallback for platforms without ffprobe: read ffmpeg's banner."""
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    text = f"{proc.stderr or ''}\n{proc.stdout or ''}"
    match = _DURATION.search(text)
    duration = 0.0
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if "Video:" not in text:
        tail = " | ".join(line.strip() for line in text.splitlines() if line.strip())[-300:]
        raise MediaError(f"cannot read {path}: {tail}")
    size = _SIZE.search(text)
    return MediaInfo(
        duration=duration,
        width=int(size.group(1)) if size else 0,
        height=int(size.group(2)) if size else 0,
        has_video=True,
        has_audio="Audio:" in text,
    )


def _probe_with_ffprobe(path: str | Path) -> MediaInfo:
    proc = subprocess.run(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe failed for {path}: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = 0.0
    for candidate in (
        data.get("format", {}).get("duration"),
        (video or {}).get("duration"),
    ):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue
    return MediaInfo(
        duration=duration,
        width=int((video or {}).get("width") or 0),
        height=int((video or {}).get("height") or 0),
        has_video=video is not None,
        has_audio=audio is not None,
    )
