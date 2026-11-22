# LiveClip

[![tests](https://github.com/blaze-techs/LiveClipz/actions/workflows/tests.yml/badge.svg)](https://github.com/blaze-techs/LiveClipz/actions/workflows/tests.yml)

Watches a live source — screen, camera, stream URL or a video file — finds the
hype moments on its own, and drops ready-to-post vertical clips while the
stream is still running.

![LiveClip UI](docs/screenshot.png)

## What it does

1. Records the source into short segments and keeps a rolling buffer.
2. Scores every 250 ms on loudness **and** on-screen motion.
3. Picks peaks that stand out from the local baseline.
4. Cuts each peak with pre/post roll, crops to 9:16 (or blurs the background),
   normalises loudness, adds fades, and can transcribe the audio and burn the
   words in as captions.
5. Shows the clip in the app the moment it is written.

No cloud, no upload, no account. Everything runs on the device.

## Install

```bash
pip install -e ".[ui]"                                  # engine + app
pip install "git+https://github.com/blaze-techs/LiveClipz"  # or straight from GitHub
liveclip doctor                                         # checks ffmpeg, numpy, kivy
```

ffmpeg must be on `PATH` (or point `AUTOCLIP_FFMPEG` / `AUTOCLIP_FFPROBE` at it).
A packaged build ships its own copy and ignores `PATH`.

## Automatic captions

Captions are written by whatever speech-to-text service you point the app at;
nothing is baked into the code. Turn them on with the **Auto captions** switch
in the app, or `--captions` on the command line.

```bash
export AUTOCLIP_ASR_KEY=...          # free tier: console.groq.com
liveclip clip stream.mp4 -o clips --captions
liveclip live screen -o clips --captions
```

Each clip gets a `.srt` next to it as well as burned-in text.

| Setting | Default | Meaning |
| --- | --- | --- |
| `AUTOCLIP_ASR_PRESET` | `groq` | `groq`, `openai`, `together`, `deepinfra`, or `local` |
| `AUTOCLIP_ASR_KEY` | – | API key (falls back to `OPENAI_API_KEY`) |
| `AUTOCLIP_ASR_URL` | preset | Any OpenAI-compatible `/audio/transcriptions` base URL |
| `AUTOCLIP_ASR_MODEL` | preset | Model name, e.g. `whisper-large-v3-turbo` |
| `AUTOCLIP_ASR_LANGUAGE` | auto | ISO code such as `en`, `fr` |

Fully offline, no key, no upload:

```bash
pip install faster-whisper
export AUTOCLIP_ASR_PRESET=local AUTOCLIP_ASR_MODEL=small
```

Captions are placed in the lower sixth of the frame with a 150 px bottom margin
and sized from the output height, so they stay clear of platform UI.

## Use

Open the app:

```bash
liveclip ui
```

Or stay on the command line:

```bash
# what would it cut?
liveclip detect stream.mp4 --sensitivity 1.8

# render clips from a recording
liveclip clip stream.mp4 -o clips --length 15 --count 3

# watch the screen and cut clips as things happen
liveclip live screen -o clips --length 12 --sensitivity 1.6

# watch a stream for 10 minutes
liveclip live rtmp://example.com/live -o clips --duration 600
```

Sources: `screen`, `camera`, any URL ffmpeg understands (`rtmp://`, `rtsp://`,
`http(s)://…m3u8`), or a local file path.

## Audio

Streams and files carry their own audio. Screen and camera capture do not, so
pass the system-sound device explicitly:

```bash
# Linux: the default PulseAudio monitor (game/app sound, not the mic)
liveclip live screen --audio-device "pulse:@DEFAULT_MONITOR@"

# Windows: a loopback device such as Stereo Mix or virtual-audio-capturer
liveclip live screen --audio-device "dshow:Stereo Mix"

# macOS: avfoundation audio index 0
liveclip live screen --audio-device "avfoundation:0"
```

The same value can be set once in `AUTOCLIP_AUDIO_DEVICE` and the app picks it
up automatically. Without it, screen clips are silent but detection still works
from motion.

## Tuning

| Flag | Meaning |
| --- | --- |
| `--sensitivity` | 1.0 = calm, 2.5 = only huge moments (default 1.6) |
| `--length` | clip length in seconds, split as pre/post roll |
| `--count` | keep only the top N clips |
| `--blur` | blurred background instead of a centre crop |
| `--horizontal` | keep the source aspect ratio |
| `--subtitles clip.srt` | burn captions into the clip |
| `--buffer` | live rolling buffer in seconds (default 120) |
| `--audio-device` | system sound for screen/camera capture |

## How detection works

Audio is decoded to mono 16 kHz PCM and reduced to RMS loudness per 250 ms
frame. Motion is the mean absolute difference between consecutive 160×90 grey
frames at 4 fps. Each signal is compared with a rolling median baseline
(45 s), scaled by the median absolute deviation, and clamped to 0..1, so a quiet
podcast and a loud game stream are judged the same way. The two curves are
blended (65 % audio, 35 % motion by default) and peaks are taken greedily,
highest first, with a minimum spacing of 6 s.

That means it reacts to cheering, shouting, sudden music and big visual cuts —
not to steady background noise.

## Live mode

The recorder writes 2-second MP4 segments plus a CSV index. The monitor thread
scores the rolling window; once a peak's full post-roll has arrived, the clip is
cut from the buffered segments with the concat demuxer. Memory stays flat no
matter how long the session runs.

## Build a Windows EXE

```bash
pip install -e ".[ui]" pyinstaller
# put ffmpeg.exe and ffprobe.exe in packaging/pyinstaller/bin/
pyinstaller --noconfirm packaging/pyinstaller/liveclip.spec
```

The result is `dist/LiveClip.exe`, with ffmpeg bundled inside. Double-clicking
opens the UI; running it with arguments (`LiveClip.exe doctor`) prints to the
calling console.

On Linux the build has to run under a virtual display, because PyInstaller's
Kivy hook imports Kivy to discover submodules:

```bash
xvfb-run -a -s "-screen 0 1024x768x24" pyinstaller --noconfirm \
  packaging/pyinstaller/liveclip.spec
```

`.github/workflows/build-desktop.yml` does all of this for Windows, macOS and
Linux on demand.

## Build an Android APK

```bash
pip install buildozer cython
buildozer -v android debug      # ~40 minutes, needs the SDK/NDK
```

The APK bundles ffmpeg and compiles `src/org/liveclip/ScreenCapture.java` into
the app. `ffpyplayer_codecs` is in the requirements because python-for-android's
ffmpeg recipe is built with `--disable-everything`; without that recipe there
are no encoders at all. CI job: `.github/workflows/build-android.yml`.

## Platform support

| Source | Windows | macOS | Linux | Android |
| --- | --- | --- | --- | --- |
| Screen | ✅ gdigrab | ✅ avfoundation | ✅ x11grab | ✅ MediaProjection |
| Camera | ✅ dshow | ✅ avfoundation | ✅ v4l2 | ❌ not wired |
| Stream URL | ✅ | ✅ | ✅ | ✅ |
| Video file | ✅ | ✅ | ✅ | ✅ |
| System audio | ✅ with `--audio-device` | ✅ | ✅ with `--audio-device` | ✅ microphone |

On Android the screen path mirrors the display into a hardware H.264 encoder
and hands ffmpeg the elementary stream over a loopback TCP port (p4a's ffmpeg
has no `unix` protocol), so clipping costs the same as on desktop. The system
asks for capture permission the first time you press Start. Microphone audio is
recorded when `RECORD_AUDIO` is granted.

That ffmpeg is also a stripped build: it only has the `crop`, `scale` and
`volume` filters. The renderer asks `ffmpeg -filters` what exists and drops what
does not, so Android clips are cropped to 9:16 but skip loudness normalisation,
fades and the blurred-background fit. Desktop builds do all of it.

Android camera capture is the one gap left: it needs a Camera2 session feeding
the same encoder, and the app currently says so instead of failing quietly.

## Tests

```bash
python -m pytest        # 40 tests, ~25 s, needs ffmpeg
python -m ruff check .
```

The suite generates its own test video (two loud flashes in 12 s) and asserts
that detection, planning, rendering and a full live session all behave. The
Android bridge is tested off-device through its socket wiring, and CI compiles
the Java helper against the real Android SDK jar.

## Layout

```
autoclip/
  config.py   tunable dataclasses
  media.py    ffmpeg/ffprobe wrappers (bundled binary aware)
  detect.py   loudness + motion scoring, peak picking
  plan.py     clip windows, merging, top-N
  clip.py     ffmpeg render (9:16, blur, captions, loudnorm)
  live.py     sources, segment recorder, rolling-buffer monitor
  android.py  MediaProjection bridge (sockets -> ffmpeg)
  cli.py      detect / clip / live / doctor / ui
  ui/app.py   Kivy app for desktop and Android
src/org/liveclip/ScreenCapture.java   Android capture service
```
