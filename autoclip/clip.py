"""Render clip windows to vertical, loudness-normalised MP4s."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Sequence

from .config import ClipConfig
from .media import probe, run
from .plan import ClipPlan

_SAFE_STYLE = (
    "FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,MarginV=140"
)


def _subtitle_filter(path: Path) -> str:
    escaped = str(Path(path).resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return f"subtitles='{escaped}':force_style='{_SAFE_STYLE}'"


def _video_filters(
    cfg: ClipConfig,
    duration: float,
    subtitles: Path | None,
) -> tuple[str | None, list[str]]:
    """Return (complex graph prefix, simple filter chain)."""
    w, h = cfg.width, cfg.height
    prefix: str | None = None
    chain: list[str] = []

    if not cfg.vertical:
        chain += [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos",
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
        ]
    elif cfg.fit == "blur":
        prefix = (
            f"split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={w}:{h},gblur=sigma=24[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        chain += [
            "crop='min(iw,ih*9/16)':'ih':'(iw-min(iw,ih*9/16))/2':0",
            f"scale={w}:{h}:flags=lanczos",
        ]

    chain.append("setsar=1")
    if subtitles is not None:
        chain.append(_subtitle_filter(subtitles))
    if cfg.fade > 0 and duration > 2 * cfg.fade:
        chain += [
            f"fade=t=in:st=0:d={cfg.fade:.2f}",
            f"fade=t=out:st={duration - cfg.fade:.2f}:d={cfg.fade:.2f}",
        ]
    return prefix, chain


def _audio_filters(cfg: ClipConfig, duration: float) -> str | None:
    if not cfg.loudness:
        return None
    chain = ["loudnorm=I=-14:TP=-1.5:LRA=11"]
    if cfg.fade > 0 and duration > 2 * cfg.fade:
        chain += [
            f"afade=t=in:st=0:d={cfg.fade:.2f}",
            f"afade=t=out:st={duration - cfg.fade:.2f}:d={cfg.fade:.2f}",
        ]
    return ",".join(chain)


def _render(
    input_args: Sequence[str],
    duration: float,
    out_path: Path,
    cfg: ClipConfig,
    has_audio: bool,
    subtitles: Path | None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prefix, chain = _video_filters(cfg, duration, subtitles)
    args: list[str] = ["-v", "error", *input_args]

    if prefix is None:
        args += ["-vf", ",".join(chain)]
    else:
        graph = f"[0:v]{prefix},{','.join(chain)}[vout]"
        args += ["-filter_complex", graph, "-map", "[vout]"]

    if has_audio:
        if prefix is not None:
            args += ["-map", "0:a:0"]
        afilter = _audio_filters(cfg, duration)
        if afilter:
            args += ["-af", afilter]
        args += ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    else:
        args += ["-an"]

    args += [
        "-c:v", "libx264",
        "-preset", cfg.preset,
        "-crf", str(cfg.crf),
        "-pix_fmt", "yuv420p",
        "-r", str(cfg.fps),
        "-movflags", "+faststart",
        str(out_path),
    ]
    run(args)
    return out_path


def render_clip(
    src: str | Path,
    start: float,
    end: float,
    out_path: str | Path,
    cfg: ClipConfig,
    subtitles: str | Path | None = None,
) -> Path:
    info = probe(src)
    duration = max(0.1, end - start)
    return _render(
        ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}"],
        duration,
        Path(out_path),
        cfg,
        info.has_audio,
        Path(subtitles) if subtitles else None,
    )


def render_segments_clip(
    segments: Sequence[Path],
    offset: float,
    length: float,
    out_path: str | Path,
    cfg: ClipConfig,
    subtitles: str | Path | None = None,
) -> Path:
    """Cut a clip out of a run of recorder segments (concat demuxer)."""
    if not segments:
        raise ValueError("no segments to render")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        for segment in segments:
            handle.write(f"file '{Path(segment).resolve().as_posix()}'\n")
        listfile = Path(handle.name)
    try:
        info = probe(segments[0])
        return _render(
            ["-f", "concat", "-safe", "0", "-i", str(listfile),
             "-ss", f"{offset:.3f}", "-t", f"{length:.3f}"],
            length,
            Path(out_path),
            cfg,
            info.has_audio,
            Path(subtitles) if subtitles else None,
        )
    finally:
        listfile.unlink(missing_ok=True)


def render_plans(
    src: str | Path,
    plans: Sequence[ClipPlan],
    out_dir: str | Path,
    cfg: ClipConfig,
    on_clip: Callable[[Path, ClipPlan], None] | None = None,
) -> list[Path]:
    out_dir = Path(out_dir)
    written: list[Path] = []
    for plan in plans:
        target = out_dir / f"clip_{int(round(plan.start * 1000)):09d}.mp4"
        render_clip(src, plan.start, plan.end, target, cfg)
        written.append(target)
        if on_clip:
            on_clip(target, plan)
    return written
