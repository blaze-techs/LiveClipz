[app]
title = LiveClip
package.name = liveclip
package.domain = org.liveclip

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,json,ttf,java
source.exclude_dirs = tests,packaging,.github,.git,dist,build,__pycache__,.pytest_cache

version = 0.1.0

requirements = python3,kivy==2.3.1,numpy,ffmpeg,pyjnius,android

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

[buildozer]
log_level = 2
warn_on_root = 1
