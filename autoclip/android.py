"""Android screen capture bridge.

The heavy lifting happens in ``src/org/liveclip/ScreenCapture.java``: the display
is mirrored into a hardware H.264 encoder whose elementary stream is pushed to a
loopback TCP port, with microphone PCM on a second port. ffmpeg reads both, so
everything downstream (segments, scoring, clipping) is the same code as on
desktop.

Loopback TCP instead of a unix socket because python-for-android's ffmpeg only
builds the file/http/hls/udp/tcp protocols.

pyjnius is imported lazily so this module stays importable off-device, which
keeps the engine and the test suite platform independent.
"""

from __future__ import annotations

import threading

from .live import Source
from .media import MediaError

REQUEST_CODE = 0x4C43
MIC_REQUEST_CODE = 0x4C44
RESULT_OK = -1
LOOPBACK = "tcp://127.0.0.1"


class AndroidCaptureError(MediaError):
    pass


def _activity():
    from jnius import autoclass

    return autoclass("org.kivy.android.PythonActivity").mActivity


class AndroidScreenSource(Source):
    """MediaProjection screen capture feeding ffmpeg over loopback ports."""

    is_file = False

    def __init__(
        self,
        *,
        width: int = 0,
        height: int = 0,
        fps: int = 30,
        bitrate: int = 6_000_000,
        with_audio: bool = True,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.bitrate = int(bitrate)
        self.with_audio = bool(with_audio)
        self._capture = None
        self._result: tuple[int, object] | None = None
        self._has_audio = False
        self._video_port = 0
        self._audio_port = 0

    # -- permissions ----------------------------------------------------
    def request_permission_async(self, on_ready, on_error=None) -> None:
        """Show the capture prompt without blocking the caller.

        Must be called on the UI thread: the activity result is delivered to the
        main looper, so blocking here would deadlock the app.
        """
        from android import activity as android_activity
        from jnius import autoclass

        activity = _activity()
        Context = autoclass("android.content.Context")
        manager = activity.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
        intent = manager.createScreenCaptureIntent()

        def fail(message: str) -> None:
            if on_error is not None:
                on_error(AndroidCaptureError(message))

        def on_result(request_code, result_code, data):
            if request_code != REQUEST_CODE:
                return
            android_activity.unbind(on_activity_result=on_result)
            if int(result_code) != RESULT_OK or data is None:
                fail("screen capture was not allowed")
                return
            self._result = (int(result_code), data)
            on_ready()

        android_activity.bind(on_activity_result=on_result)
        try:
            activity.startActivityForResult(intent, REQUEST_CODE)
        except Exception as error:
            android_activity.unbind(on_activity_result=on_result)
            fail(f"could not open the capture prompt: {error}")

    def has_permission(self) -> bool:
        return self._result is not None

    def _ensure_microphone(self) -> bool:
        if not self.with_audio:
            return False
        try:
            from android import activity as android_activity
            from jnius import autoclass

            activity = _activity()
            PackageManager = autoclass("android.content.pm.PackageManager")
            permission = "android.permission.RECORD_AUDIO"
            if activity.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED:
                return True

            answered = threading.Event()

            def on_result(permissions, grants):
                answered.set()

            android_activity.bind(on_request_permissions_result=on_result)
            try:
                activity.requestPermissions([permission], MIC_REQUEST_CODE)
                answered.wait(30)
            finally:
                android_activity.unbind(on_request_permissions_result=on_result)
            return activity.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED
        except Exception:
            return False

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._capture is not None:
            return
        if self._result is None:
            raise AndroidCaptureError(
                "screen capture needs permission from the app; open the LiveClip UI "
                "and tap Start, or pass a stream URL / video file instead"
            )
        from jnius import autoclass

        capture_class = autoclass("org.liveclip.ScreenCapture")
        result_code, data = self._result
        self._capture = capture_class.start(
            _activity(),
            result_code,
            data,
            self._ensure_microphone(),
            self.width,
            self.height,
            self.fps,
            self.bitrate,
        )
        self._video_port = int(self._capture.getVideoPort())
        self._audio_port = int(self._capture.getAudioPort())
        self._has_audio = bool(self._capture.hasAudio()) and self._audio_port > 0
        if self._video_port <= 0:
            raise AndroidCaptureError("the screen capture service did not open a port")

    def stop(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            try:
                capture.stop()
            except Exception:
                pass
        self._has_audio = False
        self._video_port = 0
        self._audio_port = 0
        self._result = None

    # -- ffmpeg wiring --------------------------------------------------
    def input_args(self) -> list[str]:
        args = ["-f", "h264", "-r", str(self.fps), "-i", f"{LOOPBACK}:{self._video_port}"]
        if self._has_audio:
            args += [
                "-f",
                "s16le",
                "-ar",
                "44100",
                "-ac",
                "1",
                "-i",
                f"{LOOPBACK}:{self._audio_port}",
            ]
        return args

    def maps(self) -> list[str]:
        if self._has_audio:
            return ["-map", "0:v:0?", "-map", "1:a:0?"]
        return ["-map", "0:v:0?"]

    def describe(self) -> str:
        audio = "mic audio" if self.with_audio else "no audio"
        size = f"{self.width}x{self.height}" if self.width and self.height else "native"
        return f"android screen projection {size}@{self.fps} ({audio})"


def screen_source(**kwargs) -> AndroidScreenSource:
    return AndroidScreenSource(**kwargs)
