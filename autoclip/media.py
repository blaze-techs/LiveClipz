"""Thin ffmpeg/ffprobe wrappers shared by the engine and the UI."""

from __future__ import annotations

import json
import os
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


def _tool(name: str) -> str:
    override = os.environ.get(f"AUTOCLIP_{name.upper()}")
    if override:
        return override
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


def probe(path: str | Path) -> MediaInfo:
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
