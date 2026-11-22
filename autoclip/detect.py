"""Highlight detection from loudness and on-screen motion.

Both signals are turned into "excitement" curves by comparing each frame with a
rolling median baseline, so a quiet stream and a loud one are scored the same
way. The two curves are then blended and peak-picked with a minimum spacing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import DetectConfig
from .media import ffmpeg_bin

SILENCE_DB = -70.0
SAMPLE_RATE = 16000
_EPS = 1e-6


@dataclass(frozen=True)
class Peak:
    time: float
    score: float


def read_audio(path: str | Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Decode mono float samples, or an empty array when there is no audio."""
    cmd = [
        ffmpeg_bin(),
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sr),
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks: list[bytes] = []
    assert proc.stdout is not None
    while True:
        block = proc.stdout.read(1 << 20)
        if not block:
            break
        chunks.append(block)
    proc.wait()
    raw = b"".join(chunks)
    if not raw:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def read_motion(
    path: str | Path,
    fps: float = 4.0,
    size: tuple[int, int] = (160, 90),
) -> np.ndarray:
    """Mean absolute frame difference per frame, in 0..1."""
    w, h = size
    cmd = [
        ffmpeg_bin(),
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-an",
        "-vf",
        f"fps={fps},scale={w}:{h},format=gray",
        "-f",
        "rawvideo",
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    frame_bytes = w * h
    out: list[float] = []
    prev: np.ndarray | None = None
    while True:
        block = proc.stdout.read(frame_bytes)
        if len(block) < frame_bytes:
            break
        frame = np.frombuffer(block, dtype=np.uint8).astype(np.float32)
        if prev is not None:
            out.append(float(np.mean(np.abs(frame - prev))) / 255.0)
        else:
            out.append(0.0)
        prev = frame
    proc.wait()
    return np.asarray(out, dtype=np.float64)


def pcm_db(samples: np.ndarray, sr: int = SAMPLE_RATE, hop: float = 0.25) -> np.ndarray:
    """Per-hop RMS loudness in dBFS."""
    if samples.size == 0:
        return np.zeros(0, dtype=np.float64)
    n = max(1, int(round(sr * hop)))
    pad = (-samples.size) % n
    if pad:
        samples = np.concatenate([samples, np.zeros(pad, dtype=samples.dtype)])
    frames = samples.reshape(-1, n).astype(np.float64)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    return np.clip(20.0 * np.log10(np.maximum(rms, 1e-6)), SILENCE_DB, 0.0)


def rolling_median(x: np.ndarray, window: int) -> np.ndarray:
    n = x.size
    if n == 0:
        return x
    w = max(1, int(window))
    if w <= 1:
        return x.copy()
    if w % 2 == 0:
        w += 1
    if w >= n:
        return np.full(n, float(np.median(x)))
    pad = w // 2
    padded = np.pad(x, pad, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, w), axis=1)


def robust_z(x: np.ndarray, window: int) -> np.ndarray:
    if x.size == 0:
        return x
    med = rolling_median(x, window)
    mad = rolling_median(np.abs(x - med), window)
    return (x - med) / (1.4826 * mad + _EPS)


def unit(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    hi = float(np.quantile(x, 0.98))
    if hi <= _EPS:
        return np.zeros_like(x)
    return np.clip(x / hi, 0.0, 1.0)


def interp_series(values: np.ndarray, t0: float, step: float, times: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return np.zeros_like(times)
    if values.size == 1:
        return np.full_like(times, float(values[0]))
    src = t0 + step * (np.arange(values.size) + 0.5)
    return np.interp(times, src, values, left=float(values[0]), right=float(values[-1]))


def score_series(
    audio_db: np.ndarray,
    audio_times: np.ndarray,
    motion: np.ndarray,
    motion_times: np.ndarray,
    cfg: DetectConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """Blend audio and motion excitement onto one uniform time grid."""
    if audio_db.size == 0 and motion.size == 0:
        return np.zeros(0), np.zeros(0)
    starts, ends = [], []
    if audio_db.size:
        starts.append(float(audio_times[0]))
        ends.append(float(audio_times[-1]))
    if motion.size:
        starts.append(float(motion_times[0]))
        ends.append(float(motion_times[-1]))
    t0, t1 = min(starts), max(ends)
    count = int(np.floor((t1 - t0) / cfg.hop))
    if count < 2:
        return np.zeros(0), np.zeros(0)
    times = t0 + cfg.hop * (np.arange(count) + 0.5)
    audio = np.interp(times, audio_times, audio_db) if audio_db.size else np.zeros_like(times)
    video = np.interp(times, motion_times, motion) if motion.size else np.zeros_like(times)
    window = max(3, int(round(cfg.baseline_window / cfg.hop)))
    audio_exc = unit(np.clip(robust_z(audio, window), 0.0, None))
    video_exc = unit(np.clip(robust_z(video, window), 0.0, None))
    score = cfg.audio_weight * audio_exc + cfg.video_weight * video_exc
    return times, np.clip(score, 0.0, 1.0)


def score_timeline(
    audio_db: np.ndarray,
    audio_t0: float,
    motion: np.ndarray,
    motion_t0: float,
    cfg: DetectConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """Convenience wrapper for contiguous signals that start at a known offset."""
    audio_times = audio_t0 + cfg.hop * (np.arange(audio_db.size) + 0.5)
    motion_times = motion_t0 + (np.arange(motion.size) + 0.5) / cfg.motion_fps
    return score_series(audio_db, audio_times, motion, motion_times, cfg)


def find_peaks(times: np.ndarray, score: np.ndarray, cfg: DetectConfig) -> list[int]:
    """Indices of standout frames, greedily thinned to ``min_gap`` seconds."""
    if score.size < 3:
        return []
    window = max(3, int(round(cfg.baseline_window / cfg.hop)))
    z = robust_z(score, window)
    candidates = np.where((z >= cfg.threshold) & (score >= cfg.min_score))[0]
    if candidates.size == 0:
        return []
    accepted: list[int] = []
    for idx in candidates[np.argsort(-score[candidates])]:
        if all(abs(times[idx] - times[j]) >= cfg.min_gap for j in accepted):
            accepted.append(int(idx))
    return sorted(accepted)


def detect_peaks(
    path: str | Path,
    cfg: DetectConfig,
    *,
    start: float = 0.0,
    duration: float | None = None,
) -> list[Peak]:
    """Analyse a media file (or a slice of it) and return hype peaks."""
    audio = read_audio(path)
    motion = read_motion(path, cfg.motion_fps, cfg.motion_size)
    audio_db = pcm_db(audio, SAMPLE_RATE, cfg.hop)
    times, score = score_timeline(audio_db, start, motion, start, cfg)
    if duration is not None:
        keep = times <= start + duration
        times, score = times[keep], score[keep]
    return [Peak(float(times[i]), float(score[i])) for i in find_peaks(times, score, cfg)]
