# Repository notes

LiveClip: detects hype moments in a live source and auto-cuts vertical clips.
Pure Python + ffmpeg, with a Kivy UI that also builds to Android.

## Commands

```bash
pip install -e ".[ui,dev]"
python -m pytest                 # 40 tests, needs ffmpeg on PATH
python -m ruff check .           # lint (config in pyproject.toml)
liveclip doctor
liveclip ui
```

Headless UI check:

```bash
xvfb-run -a -s "-screen 0 900x1000x24" python -c "
from autoclip.ui.app import LiveClipApp; LiveClipApp().build()"
```

Package a binary (Linux needs a display for PyInstaller's Kivy hook):

```bash
xvfb-run -a python -m PyInstaller --noconfirm packaging/pyinstaller/liveclip.spec
./dist/LiveClip doctor && ./dist/LiveClip live screen --length 6 --duration 20
```

Compile-check the Android helper without a device:

```bash
javac -source 8 -target 8 -bootclasspath <android.jar> \
  -d /tmp/java-out src/org/liveclip/ScreenCapture.java
```

## Architecture

- `autoclip/detect.py` — audio loudness (RMS per hop) + motion (mean abs frame
  diff) → robust z-score against a rolling median → greedy peak picking.
- `autoclip/plan.py` — expands peaks into windows, merges overlaps, splits at
  `max_length`, top-N.
- `autoclip/clip.py` — one ffmpeg call per clip; `-vf` for crop, `-filter_complex`
  for the blurred-background variant.
- `autoclip/live.py` — `Source` objects (`CommandSource`, Android projection),
  `SegmentRecorder` (ffmpeg segment muxer + CSV index) and `LiveClipper`
  (monitor thread that renders from the rolling buffer).
- `autoclip/android.py` — pyjnius bridge; ffmpeg reads `tcp://127.0.0.1:PORT`.
- `src/org/liveclip/ScreenCapture.java` — MediaProjection → MediaCodec (H.264)
  and AudioRecord (PCM) → two loopback sockets.
- `autoclip/ui/app.py` — Kivy app; talks to the engine only through an event queue.

## Gotchas

- `-segment_list_size 0` is required or ffmpeg keeps only the last 5 CSV rows.
- Rolling median must use an odd window, otherwise `sliding_window_view` returns
  a different length than the input.
- Subtitle filter paths need `:` and `'` escaped.
- Live mode trims the buffer by segment timestamps, not wall clock, so file
  sources behave like streams.
- MediaProjection consent must be requested asynchronously: the activity result
  arrives on the main looper, so blocking there deadlocks the app. The UI binds
  `on_activity_result`, then starts the clipper on a worker thread.
- Android 14 needs `registerCallback` on the projection before
  `createVirtualDisplay`, otherwise `SecurityException`.
- `prepend-sps-pps-to-idr-frames` keeps SPS/PPS in-band so ffmpeg's raw h264
  demuxer can start on a socket without a container.
- python-for-android's ffmpeg is `--disable-everything`: only the
  crop/scale/volume filters and the file/http/hls/udp/tcp protocols exist, and
  without `ffpyplayer_codecs` there are no encoders at all. Hence loopback TCP
  for capture and `has_filter()` checks in the renderer.
- Screen/camera capture carries no audio; `--audio-device` (or
  `AUTOCLIP_AUDIO_DEVICE`) adds a second input, and `Source.maps()` decides
  which stream gets mapped.
- PyInstaller's Kivy hook imports Kivy during analysis, so Linux builds need
  `xvfb-run`; the spec lists `autoclip` modules explicitly instead of
  `collect_submodules`, which would import the UI.
- A frozen build must prefer its bundled ffmpeg over `PATH` (see `_tool`).
- `numpy` is a hard dependency; `kivy` is optional and only the UI imports it.
