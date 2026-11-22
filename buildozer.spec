[app]
title = LiveClip
package.name = liveclip
package.domain = org.liveclip

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,json,ttf,java
source.exclude_dirs = tests,packaging,.github,.git,dist,build,__pycache__,.pytest_cache

version = 0.1.0

# ffpyplayer_codecs turns on every encoder/muxer/demuxer (libx264, aac,
# segment, concat). Without it p4a's ffmpeg recipe has no encoders at all.
#
# charset-normalizer is pulled in by requests, which ffpyplayer drags along.
# 3.x only ships compiled wheels, and p4a installs pure-Python modules with
# --only-binary against a cp314-android tag pip rejects, so pin the last
# release that still has a py3-none-any wheel.
requirements = python3,kivy==2.3.1,numpy,ffmpeg,ffpyplayer_codecs,pyjnius,android,charset-normalizer<3

orientation = all
fullscreen = 0
android.permissions = INTERNET,RECORD_AUDIO,CAMERA,READ_MEDIA_VIDEO,FOREGROUND_SERVICE,POST_NOTIFICATIONS
android.api = 33
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a,armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = True

# Java helpers (MediaProjection screen capture) compiled into the APK
android.add_src = src

# Android capture notes:
# - "Screen" uses MediaProjection + MediaCodec (src/org/liveclip/ScreenCapture.java)
#   and records microphone audio when RECORD_AUDIO is granted.
# - "Stream URL" and "Video file" sources use the bundled ffmpeg.
# - Camera capture is not wired yet; the app says so instead of failing.
# - p4a's ffmpeg only builds the crop/scale/volume filters, so Android clips
#   skip loudnorm, fades and the blurred-background fit; the engine checks
#   `ffmpeg -filters` and drops what is missing.

[buildozer]
log_level = 2
warn_on_root = 1
