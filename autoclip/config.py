"""Tunable knobs for detection, clipping and live capture."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class DetectConfig:
    """How hype moments are scored.

    ``threshold`` is a robust z-score: a frame must stand out from its rolling
    baseline by this many scaled median-absolute-deviations.
    """

    hop: float = 0.25
    baseline_window: float = 45.0
    threshold: float = 1.6
    min_gap: float = 6.0
    min_score: float = 0.15
    audio_weight: float = 0.65
    video_weight: float = 0.35
    motion_fps: float = 4.0
    motion_size: tuple[int, int] = (160, 90)

    def with_sensitivity(self, sensitivity: float) -> "DetectConfig":
        """Map a 0.5..3.0 UI slider onto the z-score threshold (higher = pickier)."""
        return replace(self, threshold=max(0.4, 3.2 - float(sensitivity)))


@dataclass(frozen=True)
class ClipConfig:
    pre_roll: float = 6.0
    post_roll: float = 6.0
    min_length: float = 6.0
    max_length: float = 45.0
    merge_gap: float = 2.0
    max_clips: int = 0  # 0 means "keep everything"
    vertical: bool = True
    width: int = 1080
    height: int = 1920
    fit: str = "crop"  # crop | blur
    loudness: bool = True
    fade: float = 0.25
    crf: int = 20
    preset: str = "veryfast"
    fps: int = 30

    def with_length(self, seconds: float) -> "ClipConfig":
        """Target clip length: split the window evenly and stop windows merging
        across more than ~10% of their length."""
        seconds = max(2.0, float(seconds))
        half = seconds / 2.0
        return replace(
            self,
            pre_roll=half,
            post_roll=half,
            min_length=min(self.min_length, seconds * 0.6),
            merge_gap=min(self.merge_gap, max(0.5, seconds * 0.1)),
        )


@dataclass(frozen=True)
class LiveConfig:
    segment_seconds: float = 2.0
    buffer_seconds: float = 120.0
    tick: float = 0.5
    realtime: bool = True
    confirm_delay: float = 1.5


@dataclass(frozen=True)
class Paths:
    output_dir: Path
    work_dir: Path

    def clip_path(self, start: float) -> Path:
        return self.output_dir / f"clip_{int(round(start * 1000)):09d}.mp4"
