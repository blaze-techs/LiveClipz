"""liveclip: find hype moments in a live source and auto-cut vertical clips."""

from .clip import render_clip, render_plans, render_segments_clip
from .config import ClipConfig, DetectConfig, LiveConfig
from .detect import Peak, detect_peaks, find_peaks, score_timeline
from .live import LiveClipper, SegmentRecorder
from .plan import ClipPlan, plan_clips

__version__ = "0.1.0"

__all__ = [
    "ClipConfig",
    "ClipPlan",
    "DetectConfig",
    "LiveClipper",
    "LiveConfig",
    "Peak",
    "SegmentRecorder",
    "detect_peaks",
    "find_peaks",
    "plan_clips",
    "render_clip",
    "render_plans",
    "render_segments_clip",
    "score_timeline",
]
