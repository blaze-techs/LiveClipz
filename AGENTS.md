# Repository notes

LiveClip: detects hype moments in a live source and auto-cuts vertical clips.
Pure Python + ffmpeg, with a Kivy UI that also builds to Android.

## Commands

```bash
pip install -e ".[ui,dev]"
python -m pytest            # 31 tests, needs ffmpeg on PATH
liveclip doctor             # runtime check
liveclip ui                 # desktop app
```

Headless UI check (needs xvfb):

```bash
xvfb-run -a -s "-screen 0 900x1000x24" python -c "
from autoclip.ui.app import LiveClipApp; LiveClipApp().build()"
```

## Architecture

- `autoclip/detect.py` — audio loudness (RMS per hop) + motion (mean abs frame
  diff) → robust z-score against a rolling median → greedy peak picking.
- `autoclip/plan.py` — expands peaks into windows, merges overlaps, top-N.
- `autoclip/clip.py` — one ffmpeg call per clip; `-vf` for crop, `-filter_complex`
  for the blurred-background variant.
- `autoclip/live.py` — `SegmentRecorder` (ffmpeg segment muxer + CSV index) and
  `LiveClipper` (monitor thread that renders from the rolling buffer).
- `autoclip/ui/app.py` — Kivy app; talks to the engine only through an event queue.

## Gotchas

- `-segment_list_size 0` is required or ffmpeg keeps only the last 5 CSV rows.
- Rolling median must use an odd window, otherwise `sliding_window_view` returns
  a different length than the input.
- Subtitle filter paths need `:` and `'` escaped.
- Live mode trims the buffer by segment timestamps, not wall clock, so file
  sources behave like streams.
- `numpy` is a hard dependency; `kivy` is optional and imported only by the UI.
