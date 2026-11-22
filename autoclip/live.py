"""Live mode: record in short segments, watch them, auto-cut clips.

The recorder writes 2-second segments into a working directory and keeps a CSV
index of them. The monitor thread scores the rolling buffer, and as soon as a
peak has its full post-roll available it renders the clip from the buffered
segments. Nothing is buffered in memory, so a long session stays cheap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

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


def resolve_source(source: str) -> tuple[list[str], bool]:
    """Map a friendly source name onto ffmpeg input args.

    Returns ``(input_args, is_file)`` where ``is_file`` means the input should be
    paced with ``-re`` to behave like a live feed.
    """
    key = source.strip().lower()
    if is_android() and key in {"screen", "desktop", "camera"}:
        raise MediaError(
            "Screen and camera capture on Android need the MediaProjection service, "
            "which ships in v0.2. Use a stream URL or a video file for now."
        )
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


class SegmentRecorder:
    def __init__(
        self,
        source: str,
        work_dir: str | Path,
        *,
        segment_seconds: float = 2.0,
        realtime: bool = True,
        fps: int = 30,
    ) -> None:
        self.source = source
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.segment_seconds = segment_seconds
        self.realtime = realtime
        self.fps = fps
        self.index = self.work_dir / "segments.csv"
        self._proc: subprocess.Popen | None = None
        self._stderr: list[str] = []

    def command(self) -> list[str]:
        input_args, is_file = resolve_source(self.source)
        gop = max(2, int(round(self.segment_seconds * self.fps)))
        args = ["-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
        if is_file and self.realtime:
            args += ["-re"]
        args += input_args
        args += [
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-f", "segment",
            "-segment_time", f"{self.segment_seconds:.3f}",
            "-reset_timestamps", "1",
            "-segment_list", str(self.index),
            "-segment_list_type", "csv",
            "-segment_list_size", "0",
            "-segment_format", "mp4",
            str(self.work_dir / "seg_%05d.mp4"),
        ]
        return [ffmpeg_bin(), *args]

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self.command(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None

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
        source: str,
        out_dir: str | Path,
        *,
        detect: DetectConfig | None = None,
        clip: ClipConfig | None = None,
        live: LiveConfig | None = None,
        work_dir: str | Path | None = None,
        subtitles: str | Path | None = None,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.source = source
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.detect = detect or DetectConfig()
        self.clip = clip or ClipConfig()
        self.live = live or LiveConfig()
        self.subtitles = Path(subtitles) if subtitles else None
        self.on_event = on_event

        base = Path(work_dir) if work_dir else self.out_dir / ".work"
        self.work_dir = base
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.recorder = SegmentRecorder(
            source, self.work_dir, segment_seconds=self.live.segment_seconds, realtime=self.live.realtime
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
                time.sleep(max(0.0, self.live.tick))
                if not self.recorder.is_running():
                    break
            self._stop.wait(self.live.tick)
        try:
            self.tick()
        except Exception:
            pass

    def start(self) -> None:
        self.recorder.start()
        self._emit({"type": "state", "state": "live", "source": self.source})
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
        self._emit({"type": "state", "state": "live", "source": self.source})
        self._loop(deadline)
        self.recorder.stop()
        self._emit({"type": "state", "state": "idle"})
        return list(self.clips)


def probe_source(source: str) -> str:
    """Human-readable description used by the CLI and UI."""
    args, is_file = resolve_source(source)
    return f"{'file' if is_file else 'live'} input: {' '.join(args)}"
