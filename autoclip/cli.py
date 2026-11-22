"""Command line entry point: detect, clip, live, doctor, ui."""

from __future__ import annotations

import argparse
import json
import sys
from importlib.util import find_spec
from pathlib import Path

from .clip import render_plans
from .config import ClipConfig, DetectConfig, LiveConfig
from .detect import detect_peaks
from .live import LiveClipper, probe_source
from .media import MediaError, have_ffmpeg, probe
from .plan import plan_clips


def _add_detect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sensitivity", type=float, default=1.6,
        help="1.0 = calm, 2.5 = only huge moments (default 1.6)",
    )
    parser.add_argument("--min-gap", type=float, default=6.0, help="seconds between clips")


def _add_clip_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--out", default="clips", help="output directory")
    parser.add_argument("--length", type=float, default=12.0, help="clip length in seconds")
    parser.add_argument("--count", type=int, default=0, help="keep only the top N clips")
    parser.add_argument("--horizontal", action="store_true", help="keep the source aspect ratio")
    parser.add_argument("--blur", action="store_true", help="blurred background instead of crop")
    parser.add_argument("--subtitles", help="SRT file burned into the clip")


def _clip_config(args: argparse.Namespace) -> ClipConfig:
    cfg = ClipConfig(
        max_clips=args.count,
        vertical=not args.horizontal,
        fit="blur" if args.blur else "crop",
    )
    return cfg.with_length(args.length)


def _detect_config(args: argparse.Namespace) -> DetectConfig:
    return DetectConfig(min_gap=args.min_gap).with_sensitivity(args.sensitivity)


def cmd_detect(args: argparse.Namespace) -> int:
    cfg = _detect_config(args)
    info = probe(args.input)
    peaks = detect_peaks(args.input, cfg)
    plans = plan_clips(peaks, _clip_config(args), duration=info.duration or None)
    if args.json:
        print(json.dumps(
            {
                "input": str(args.input),
                "duration": info.duration,
                "peaks": [{"time": round(p.time, 2), "score": round(p.score, 3)} for p in peaks],
                "clips": [
                    {"start": round(c.start, 2), "end": round(c.end, 2), "score": round(c.score, 3)}
                    for c in plans
                ],
            },
            indent=2,
        ))
        return 0
    print(f"{args.input}: {info.duration:.1f}s, {len(peaks)} peaks, {len(plans)} clips")
    for clip in plans:
        print(f"  {clip.start:7.2f}s -> {clip.end:7.2f}s  score {clip.score:.2f}")
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    cfg = _clip_config(args)
    info = probe(args.input)
    peaks = detect_peaks(args.input, _detect_config(args))
    plans = plan_clips(peaks, cfg, duration=info.duration or None)
    if not plans:
        print("No hype moments found. Lower --sensitivity or use a longer input.")
        return 1
    out = Path(args.out)
    print(f"Rendering {len(plans)} clip(s) into {out}/ ...")
    written = render_plans(
        args.input, plans, out, cfg,
        on_clip=lambda path, plan: print(f"  + {path.name}  {plan.duration:.1f}s  score {plan.score:.2f}"),
    )
    print(f"Done: {len(written)} clip(s).")
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    clip_cfg = _clip_config(args)
    live_cfg = LiveConfig(
        segment_seconds=args.segment,
        buffer_seconds=args.buffer,
        realtime=True,
        confirm_delay=0.0 if Path(args.source).exists() else 1.5,
    )
    print(f"Source: {probe_source(args.source)}")

    def report(event: dict) -> None:
        if event["type"] == "clip":
            print(f"  + {Path(event['path']).name}  {event['end'] - event['start']:.1f}s  "
                  f"score {event['score']:.2f}")
        elif event["type"] == "error":
            print(f"  ! {event['message']}", file=sys.stderr)
        elif event["type"] == "state":
            print(f"[{event['state']}]")

    clipper = LiveClipper(
        args.source, args.out,
        detect=_detect_config(args),
        clip=clip_cfg,
        live=live_cfg,
        subtitles=args.subtitles,
        on_event=report,
    )
    clips = clipper.run(duration=args.duration)
    print(f"Session finished: {len(clips)} clip(s) in {args.out}/")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    ok = have_ffmpeg()
    print(f"ffmpeg: {'found' if ok else 'MISSING'}")
    if not ok:
        print("Install ffmpeg (https://ffmpeg.org) or set AUTOCLIP_FFMPEG.")
        return 1
    from .media import ffmpeg_bin

    print(f"  {ffmpeg_bin()}")
    if find_spec("numpy") is None:
        print("numpy: MISSING (pip install numpy)")
        return 1
    import numpy

    print(f"numpy: {numpy.__version__}")
    if find_spec("kivy") is not None:
        print("kivy: found (UI available)")
    else:
        print("kivy: not installed (CLI only; pip install 'liveclip[ui]')")
    return 0


def cmd_ui(_: argparse.Namespace) -> int:
    from .ui.app import main

    main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liveclip",
        description="Detect hype moments in a live source and auto-cut vertical clips.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="list hype moments in a file")
    detect.add_argument("input")
    detect.add_argument("--json", action="store_true")
    _add_detect_args(detect)
    _add_clip_args(detect)
    detect.set_defaults(func=cmd_detect)

    clip = sub.add_parser("clip", help="render clips from a file")
    clip.add_argument("input")
    _add_detect_args(clip)
    _add_clip_args(clip)
    clip.set_defaults(func=cmd_clip)

    live = sub.add_parser("live", help="watch a live source and auto-cut clips")
    live.add_argument("source", nargs="?", default="screen",
                      help="screen | camera | stream URL | video file")
    live.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    live.add_argument("--buffer", type=float, default=120.0, help="rolling buffer seconds")
    live.add_argument("--segment", type=float, default=2.0, help="segment length seconds")
    _add_detect_args(live)
    _add_clip_args(live)
    live.set_defaults(func=cmd_live)

    doctor = sub.add_parser("doctor", help="check the runtime")
    doctor.set_defaults(func=cmd_doctor)

    ui = sub.add_parser("ui", help="open the desktop/mobile app")
    ui.set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MediaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopped")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
