"""Live mode: record in short segments, watch them, auto-cut clips.

The recorder writes 2-second segments into a working directory and keeps a CSV
index of them. The monitor thread scores the rolling buffer, and as soon as a
peak has its full post-roll available it renders the clip from the buffered
segments. Nothing is buffered in memory, so a long session stays cheap.

Sources are objects: a plain command source hands ffmpeg a URL or device, while
the Android screen source produces an H.264 stream on a socket that ffmpeg
reads. Everything downstream is identical.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .clip import render_segments_clip
from .config import ClipConfig, DetectConfig, LiveConfig
from .detect import (
    SAMPLE_RATE,
    find_peaks,
    pcm_db,
    read_audio,
    read_motion,
    score_series,
)
from .media import MediaError, ffmpeg_bin

Event = dict


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float
    end: float


def is_android() -> bool:
    return "ANDROID_ARGUMENT" in os.environ or "ANDROID_APP_PATH" in os.environ


_AUDIO_FORMATS = {
    "pulse",
    "alsa",
    "dshow",
    "avfoundation",
    "wasapi",
    "coreaudio",
    "openal",
}


def audio_input(device: str) -> list[str]:
    """ffmpeg args for a system-audio device.

    Accepts ``fmt:device`` (``pulse:@DEFAULT_MONITOR@``) or a bare device name
    that is resolved to the platform's usual capture backend.
    """
    if ":" in device and device.split(":", 1)[0] in _AUDIO_FORMATS:
        fmt, name = device.split(":", 1)
        return ["-f", fmt, "-i", name]
    if sys.platform.startswith("linux"):
        return ["-f", "pulse", "-i", device]
    if sys.platform.startswith("win"):
        return ["-f", "dshow", "-i", f"audio={device}"]
    if sys.platform == "darwin":
        return ["-f", "avfoundation", "-i", device]
    return ["-i", device]


def resolve_source(source: str, audio_device: str | None = None) -> tuple[list[str], bool]:
    """Map a friendly source name onto ffmpeg input args.

    Returns ``(input_args, is_file)`` where ``is_file`` means the input should be
    paced with ``-re`` to behave like a live feed. ``audio_device`` adds a
    second input carrying system sound, which screen capture does not provide
    on its own.
    """
    args, is_file = _resolve_video_source(source)
    if audio_device:
        args = args + audio_input(audio_device)
    return args, is_file


def _resolve_video_source(source: str) -> tuple[list[str], bool]:
    key = source.strip().lower()
    if key in {"screen", "desktop"}:
        if sys.platform.startswith("linux"):
            display = os.environ.get("AUTOCLIP_DISPLAY") or os.environ.get("DISPLAY", ":0.0")
            return ["-f", "x11grab", "-framerate", "30", "-i", display], False
        if sys.platform.startswith("win"):
            return ["-f", "gdigrab", "-framerate", "30", "-i", "desktop"], False
        if sys.platform == "darwin":
            return ["-f", "avfoundation", "-framerate", "30", "-i", "1:none"], False
        raise MediaError(f"screen capture is not supported on {sys.platform}")
    if key == "camera":
        if sys.platform.startswith("linux"):
            device = os.environ.get("AUTOCLIP_CAMERA_DEVICE", "/dev/video0")
            return ["-f", "v4l2", "-framerate", "30", "-i", device], False
        if sys.platform.startswith("win"):
            device = os.environ.get("AUTOCLIP_CAMERA_DEVICE", "Integrated Camera")
            return ["-f", "dshow", "-framerate", "30", "-i", f"video={device}"], False
        if sys.platform == "darwin":
            return ["-f", "avfoundation", "-framerate", "30", "-i", "0:0"], False
        raise MediaError(f"camera capture is not supported on {sys.platform}")
    return ["-i", source], Path(source).expanduser().exists()


class Source:
    """Something ffmpeg can read, possibly needing a producer process."""

    is_file = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def input_args(self) -> list[str]:
        raise NotImplementedError

    def maps(self) -> list[str]:
        return ["-map", "0:v:0?", "-map", "0:a:0?"]

    def describe(self) -> str:
        return " ".join(self.input_args())


class CommandSource(Source):
    def __init__(self, source: str, audio_device: str | None = None) -> None:
        self.source = source
        self.audio_device = audio_device
        self._args, self.is_file = resolve_source(source, audio_device)

    def input_args(self) -> list[str]:
        return list(self._args)

    def maps(self) -> list[str]:
        if self.audio_device and self._args.count("-i") > 1:
            return ["-map", "0:v:0?", "-map", "1:a:0?"]
        return super().maps()

    def describe(self) -> str:
        kind = "file" if self.is_file else "live"
        extra = f" + audio {self.audio_device}" if self.audio_device else ""
        return f"{kind} input: {' '.join(self._args)}{extra}"


def open_source(source: str, audio_device: str | None = None) -> Source:
    """Resolve a source name, using the Android projection bridge when needed."""
    key = source.strip().lower()
    if key in {"screen", "desktop", "camera"} and not audio_device:
        audio_device = os.environ.get("AUTOCLIP_AUDIO_DEVICE") or None
    if is_android():
        if key in {"screen", "desktop"}:
            from .android import screen_source

            return screen_source()
        if key == "camera":
            raise MediaError(
                "camera capture is not wired on Android yet; use screen, a stream URL "
                "or a video file"
            )
    return CommandSource(source, audio_device)


class SegmentRecorder:
    def __init__(
        self,
        source: str | Source,
        work_dir: str | Path,
        *,
        segment_seconds: float = 2.0,
        realtime: bool = True,
        fps: int = 30,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.source = source if isinstance(source, Source) else CommandSource(source)
        self.segment_seconds = segment_seconds
        self.realtime = realtime
        self.fps = fps
        self.index = self.work_dir / "segments.csv"
        self.log_path = self.work_dir / "recorder.log"
        self._proc: subprocess.Popen | None = None
        self._log = None

    def command(self) -> list[str]:
        gop = max(2, int(round(self.segment_seconds * self.fps)))
        args = ["-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
        if self.source.is_file and self.realtime:
            args += ["-re"]
        args += self.source.input_args()
        args += self.source.maps()
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-f",
            "segment",
            "-segment_time",
            f"{self.segment_seconds:.3f}",
            "-reset_timestamps",
            "1",
            "-segment_list",
            str(self.index),
            "-segment_list_type",
            "csv",
            "-segment_list_size",
            "0",
            "-segment_format",
            "mp4",
            str(self.work_dir / "seg_%05d.mp4"),
        ]
        return [ffmpeg_bin(), *args]

    def start(self) -> None:
        self.source.start()
        self._log = open(self.log_path, "w+", encoding="utf-8", errors="replace")
        self._proc = subprocess.Popen(self.command(), stdout=subprocess.DEVNULL, stderr=self._log)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                message = self.last_error()
                self.stop()
                raise MediaError(f"capture failed to start: {message or 'ffmpeg exited'}")
            time.sleep(0.05)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def last_error(self) -> str:
        if self._log is None:
            return ""
        try:
            self._log.flush()
            size = os.fstat(self._log.fileno()).st_size
            self._log.seek(max(0, size - 4000))
            text = self._log.read()
        except (OSError, ValueError):
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return " | ".join(lines[-3:])[:400]

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
            self._proc = None
        self.source.stop()
        if self._log is not None:
            self._log.close()
            self._log = None

    def segments(self) -> list[Segment]:
        if not self.index.exists():
            return []
        found: list[Segment] = []
        for line in self.index.read_text(errors="ignore").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 3:
                continue
            name, start, end = parts[0], parts[1], parts[2]
            path = Path(name)
            if not path.is_absolute():
                path = self.work_dir / path.name
            try:
                start_f, end_f = float(start), float(end)
            except ValueError:
                continue
            if path.exists() and end_f > start_f:
                found.append(Segment(path, start_f, end_f))
        return sorted(found, key=lambda s: s.start)

    def discard_before(self, cutoff: float, keep: Sequence[Segment] = ()) -> None:
        protected = {s.path for s in keep}
        for segment in self.segments():
            if segment.end <= cutoff and segment.path not in protected:
                segment.path.unlink(missing_ok=True)


class LiveClipper:
    """Watches a live source and writes a clip for every hype moment."""

    def __init__(
        self,
        source: str | Source,
        out_dir: str | Path,
        *,
        detect: DetectConfig | None = None,
        clip: ClipConfig | None = None,
        live: LiveConfig | None = None,
        work_dir: str | Path | None = None,
        subtitles: str | Path | None = None,
        captions: bool = False,
        audio_device: str | None = None,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.source = source if isinstance(source, Source) else open_source(source, audio_device)
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.detect = detect or DetectConfig()
        self.clip = clip or ClipConfig()
        self.live = live or LiveConfig()
        self.subtitles = Path(subtitles) if subtitles else None
        self.captions = captions
        self.on_event = on_event

        base = Path(work_dir) if work_dir else self.out_dir / ".work"
        self.work_dir = base
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.recorder = SegmentRecorder(
            self.source,
            self.work_dir,
            segment_seconds=self.live.segment_seconds,
            realtime=self.live.realtime,
        )
        self._audio: list[np.ndarray] = []
        self._audio_times: list[np.ndarray] = []
        self._motion: list[np.ndarray] = []
        self._motion_times: list[np.ndarray] = []
        self._seen: set[float] = set()
        self._last_emitted = float("-inf")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.clips: list[Path] = []

    # -- events ---------------------------------------------------------
    def _emit(self, event: Event) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    # -- feature collection ---------------------------------------------
    def _ingest(self, segment: Segment) -> None:
        if segment.start in self._seen:
            return
        self._seen.add(segment.start)
        db = pcm_db(read_audio(segment.path), SAMPLE_RATE, self.detect.hop)
        if db.size:
            self._audio.append(db)
            self._audio_times.append(segment.start + self.detect.hop * (np.arange(db.size) + 0.5))
        motion = read_motion(segment.path, self.detect.motion_fps, self.detect.motion_size)
        if motion.size:
            self._motion.append(motion)
            self._motion_times.append(
                segment.start + (np.arange(motion.size) + 0.5) / self.detect.motion_fps
            )

    def _prune(self, cutoff: float) -> None:
        def keep_latest(arrays: list[np.ndarray], times: list[np.ndarray]) -> tuple[list, list]:
            kept_a, kept_t = [], []
            for values, stamps in zip(arrays, times):
                if stamps.size and stamps[-1] > cutoff:
                    kept_a.append(values)
                    kept_t.append(stamps)
            return kept_a, kept_t

        self._audio, self._audio_times = keep_latest(self._audio, self._audio_times)
        self._motion, self._motion_times = keep_latest(self._motion, self._motion_times)

    def _timeline(self) -> tuple[np.ndarray, np.ndarray]:
        audio = np.concatenate(self._audio) if self._audio else np.zeros(0)
        audio_t = np.concatenate(self._audio_times) if self._audio_times else np.zeros(0)
        motion = np.concatenate(self._motion) if self._motion else np.zeros(0)
        motion_t = np.concatenate(self._motion_times) if self._motion_times else np.zeros(0)
        return score_series(audio, audio_t, motion, motion_t, self.detect)

    # -- one monitoring pass --------------------------------------------
    def tick(self) -> list[Path]:
        segments = self.recorder.segments()
        for segment in segments:
            self._ingest(segment)
        if not segments:
            return []

        latest = segments[-1].end
        cutoff = latest - self.live.buffer_seconds
        self._prune(cutoff)

        times, score = self._timeline()
        written: list[Path] = []
        for idx in find_peaks(times, score, self.detect):
            peak_time = float(times[idx])
            if peak_time <= self._last_emitted + self.detect.min_gap:
                continue
            end = peak_time + self.clip.post_roll
            if end > latest - self.live.confirm_delay:
                continue
            start = max(0.0, peak_time - self.clip.pre_roll)
            end = min(end, latest)
            if end - start < self.clip.min_length:
                continue
            cover = [s for s in segments if s.end > start and s.start < end]
            if not cover:
                continue
            target = self.out_dir / f"clip_{int(round(start * 1000)):09d}.mp4"
            try:
                render_segments_clip(
                    [s.path for s in cover],
                    offset=max(0.0, start - cover[0].start),
                    length=end - start,
                    out_path=target,
                    cfg=self.clip,
                    subtitles=self.subtitles,
                    captions=self.captions,
                )
            except MediaError as exc:
                self._emit({"type": "error", "message": str(exc)})
                continue
            self._last_emitted = peak_time
            self.clips.append(target)
            written.append(target)
            self._emit(
                {
                    "type": "clip",
                    "path": str(target),
                    "start": start,
                    "end": end,
                    "peak": peak_time,
                    "score": float(score[idx]),
                }
            )
            self._emit({"type": "level", "score": float(score[idx]), "time": peak_time})

        self.recorder.discard_before(cutoff, keep=segments[-1:])
        return written

    # -- lifecycle ------------------------------------------------------
    def _loop(self, deadline: float | None) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # keep the session alive on a bad segment
                self._emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            if deadline is not None and time.monotonic() >= deadline:
                break
            if not self.recorder.is_running():
                message = self.recorder.last_error()
                if message:
                    self._emit({"type": "error", "message": message})
                break
            self._stop.wait(self.live.tick)
        try:
            self.tick()
        except Exception:
            pass

    def start(self) -> None:
        self.recorder.start()
        self._emit({"type": "state", "state": "live", "source": self.source.describe()})
        self._thread = threading.Thread(target=self._loop, args=(None,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=60)
            self._thread = None
        self.recorder.stop()
        self._emit({"type": "state", "state": "idle"})

    def run(self, duration: float | None = None) -> list[Path]:
        """Blocking session; ``duration`` limits it in seconds."""
        deadline = time.monotonic() + duration if duration else None
        self.recorder.start()
        self._emit({"type": "state", "state": "live", "source": self.source.describe()})
        self._loop(deadline)
        self.recorder.stop()
        self._emit({"type": "state", "state": "idle"})
        return list(self.clips)


def probe_source(source: str, audio_device: str | None = None) -> str:
    """Human-readable description used by the CLI and UI."""
    return open_source(source, audio_device).describe()
