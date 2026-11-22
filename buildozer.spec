[app]
title = LiveClip
package.name = liveclip
package.domain = org.liveclip

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,json,ttf
source.exclude_dirs = tests,packaging,.github,.git,dist,build,__pycache__,.pytest_cache

version = 0.1.0

requirements = python3,kivy==2.3.1,numpy,ffmpeg,pyjnius,android

orientation = portrait
fullscreen = 0
android.permissions = INTERNET,RECORD_AUDIO,CAMERA,READ_MEDIA_VIDEO,FOREGROUND_SERVICE,POST_NOTIFICATIONS
android.api = 33
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a,armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = True

# Android capture notes:
# - "Stream URL" and "Video file" sources work with the bundled ffmpeg.
# - Full screen capture needs the MediaProjection service (v0.2 roadmap).

[buildozer]
log_level = 2
warn_on_root = 1
