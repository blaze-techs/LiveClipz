"""Turn detected peaks into non-overlapping clip windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .config import ClipConfig
from .detect import Peak


@dataclass(frozen=True)
class ClipPlan:
    start: float
    end: float
    peak: float
    score: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_clips(
    peaks: Sequence[Peak],
    cfg: ClipConfig,
    duration: float | None = None,
) -> list[ClipPlan]:
    windows: list[ClipPlan] = []
    for peak in peaks:
        start = peak.time - cfg.pre_roll
        end = peak.time + cfg.post_roll
        if duration is not None:
            start = max(0.0, start)
            end = min(duration, end)
        if end - start > cfg.max_length:
            start = peak.time - cfg.max_length / 2.0
            end = peak.time + cfg.max_length / 2.0
            if duration is not None:
                start = max(0.0, start)
                end = min(duration, end)
        if end - start < cfg.min_length or end <= 0:
            continue
        windows.append(ClipPlan(start, end, peak.time, peak.score))

    merged: list[ClipPlan] = []
    for clip in sorted(windows, key=lambda c: c.start):
        if merged and clip.start - merged[-1].end <= cfg.merge_gap:
            prev = merged[-1]
            merged[-1] = ClipPlan(
                start=prev.start,
                end=max(prev.end, clip.end),
                peak=prev.peak if prev.score >= clip.score else clip.peak,
                score=max(prev.score, clip.score),
            )
        else:
            merged.append(clip)

    if cfg.max_length > 0:
        split: list[ClipPlan] = []
        for clip in merged:
            start = clip.start
            while start < clip.end - 1e-6:
                end = min(start + cfg.max_length, clip.end)
                if end - start >= cfg.min_length:
                    split.append(ClipPlan(start, end, clip.peak, clip.score))
                start = end
        merged = split

    if cfg.max_clips and len(merged) > cfg.max_clips:
        top = sorted(merged, key=lambda c: -c.score)[: cfg.max_clips]
        merged = sorted(top, key=lambda c: c.start)
    return merged
